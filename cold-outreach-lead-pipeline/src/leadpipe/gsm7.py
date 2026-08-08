"""GSM-7 encoding checks for SMS bodies.

An SMS encoded in GSM-7 fits 160 characters per segment. One character outside
that alphabet switches the ENTIRE message to UCS-2, which fits 70 — so a
153-character message with a single curly apostrophe stops being one segment
and becomes three. You pay per segment, per recipient. On a list of ten
thousand, one invisible character is a real bill.

The characters that cause it are almost never typed deliberately. They arrive
when copy is written in a word processor or a notes app that silently converts
straight quotes to curly ones, hyphens to en dashes, and "..." to a single
ellipsis glyph. On screen the message looks identical.

`sanitize()` rewrites the ones with an unambiguous ASCII equivalent.
`scan()` reports whatever is left, which is the set of choices you have to
make on purpose — emoji, accented characters, non-Latin scripts.
"""
from __future__ import annotations

# The GSM 03.38 basic alphabet.
GSM7_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)

# Extension-table characters. Legal in GSM-7 but each costs TWO characters of
# the 160, because they are sent as an escape sequence.
GSM7_EXTENDED = set("^{}\\[~]|€")

# Characters with a safe, meaning-preserving ASCII equivalent. This map is the
# whole point of the module: it removes the failures nobody chose.
SUBSTITUTIONS = {
    "‘": "'",   # left single quote
    "’": "'",   # right single quote / curly apostrophe
    "‚": "'",
    "‛": "'",
    "′": "'",   # prime
    "“": '"',   # left double quote
    "”": '"',   # right double quote
    "„": '"',
    "″": '"',
    "–": "-",   # en dash
    "—": "-",   # em dash
    "‒": "-",
    "―": "-",
    "−": "-",   # minus sign
    "…": "...",  # ellipsis
    " ": " ",   # non-breaking space
    " ": " ",
    " ": " ",
    " ": " ",
    " ": " ",
    "​": "",    # zero-width space
    "‌": "",
    "‍": "",    # zero-width joiner (also glues emoji sequences)
    "﻿": "",    # byte-order mark
    "•": "-",   # bullet
    "«": '"',
    "»": '"',
    "‹": "'",
    "›": "'",
    "ʼ": "'",   # modifier letter apostrophe
    "⁄": "/",   # fraction slash
    "­": "",    # soft hyphen
    "\t": " ",
}

SEGMENT_GSM7_SINGLE = 160
SEGMENT_GSM7_MULTI = 153   # concatenated messages lose 7 chars to the header
SEGMENT_UCS2_SINGLE = 70
SEGMENT_UCS2_MULTI = 67


def sanitize(text: str) -> tuple:
    """Replace safely-substitutable characters. Returns (clean, replacements).

    `replacements` is a list of (original, replacement) so a report can say
    what was changed rather than silently rewriting the operator's copy.
    """
    if not text:
        return "", []
    out = []
    changed = []
    for ch in text:
        sub = SUBSTITUTIONS.get(ch)
        if sub is None:
            out.append(ch)
        else:
            out.append(sub)
            changed.append((ch, sub))
    return "".join(out), changed


def non_gsm7_chars(text: str) -> list:
    """Characters that would force UCS-2, in order of first appearance."""
    seen = []
    for ch in text or "":
        if ch in GSM7_BASIC or ch in GSM7_EXTENDED:
            continue
        if ch not in seen:
            seen.append(ch)
    return seen


def is_gsm7(text: str) -> bool:
    return not non_gsm7_chars(text)


def encoded_length(text: str) -> int:
    """Length in GSM-7 units. Extension characters count as two."""
    return sum(2 if ch in GSM7_EXTENDED else 1 for ch in text or "")


def segments(text: str) -> tuple:
    """Return (encoding, segment_count) for a rendered body."""
    bad = non_gsm7_chars(text)
    if bad:
        n = len(text or "")
        if n <= SEGMENT_UCS2_SINGLE:
            return "UCS-2", 1
        return "UCS-2", -(-n // SEGMENT_UCS2_MULTI)
    n = encoded_length(text)
    if n <= SEGMENT_GSM7_SINGLE:
        return "GSM-7", 1
    return "GSM-7", -(-n // SEGMENT_GSM7_MULTI)


def _is_emoji(ch: str) -> bool:
    o = ord(ch)
    return (
        0x1F000 <= o <= 0x1FAFF      # pictographs, emoticons, symbols
        or 0x2600 <= o <= 0x27BF     # misc symbols and dingbats
        or o in (0x2705, 0x274C, 0xFE0F, 0x20E3)
        or 0x1F1E6 <= o <= 0x1F1FF   # regional indicators (flags)
    )


def check(text: str, auto_sanitize: bool = True, allow_emoji: bool = False) -> dict:
    """Full encoding verdict for one rendered message body.

    Returns the sanitized body, what was substituted, what still forces UCS-2,
    the resulting encoding and segment count, and whether it passes.
    """
    body, replaced = (sanitize(text) if auto_sanitize else (text or "", []))
    offenders = non_gsm7_chars(body)
    emoji = [c for c in offenders if _is_emoji(c)]
    other = [c for c in offenders if not _is_emoji(c)]

    encoding, count = segments(body)
    ok = not other and (allow_emoji or not emoji)
    return {
        "body": body,
        "ok": ok,
        "replaced": replaced,
        "emoji": emoji,
        "other_non_gsm7": other,
        "encoding": encoding,
        "segments": count,
        "length": len(body),
    }


def describe(chars) -> str:
    """Human-readable listing of offending characters, with code points.

    The code point matters: on a terminal a curly apostrophe and a straight
    one are one pixel apart, and U+2019 is unambiguous.
    """
    return ", ".join("%r (U+%04X)" % (c, ord(c)) for c in chars)
