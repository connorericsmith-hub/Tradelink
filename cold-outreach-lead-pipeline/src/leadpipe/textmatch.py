"""Word-boundary term matching shared by the filter and the categorizer.

Deny lists are matched on word boundaries, not substrings. The difference is
not cosmetic: a naive `"pool" in name` drops "Liverpool Kitchens", and a naive
`"spa" in name` drops every business with "Spacious" in its name. Those are
silent false drops — the lead never appears in any report, so the mistake is
invisible until you wonder why a whole city produced nothing.
"""
from __future__ import annotations

import re


def compile_terms(terms, whole_word=()) -> dict:
    """Compile deny/allow terms to anchored regexes.

    Default is a LEADING boundary only, so "roof" matches "roofing" and
    "roofer" — trades name themselves with suffixes. Terms in `whole_word`
    get a trailing boundary too, for words short or common enough that the
    prefix match produces false drops.
    """
    whole = set(whole_word or ())
    out = {}
    for term in terms or ():
        t = str(term).lower().strip()
        if not t:
            continue
        pattern = r"\b" + re.escape(t) + (r"\b" if t in whole else "")
        out[t] = re.compile(pattern)
    return out


def first_hit(text: str, compiled: dict, terms=None) -> str | None:
    """First term from `terms` (default: all) whose regex matches `text`."""
    if not text:
        return None
    for term in (terms if terms is not None else compiled.keys()):
        rx = compiled.get(term)
        if rx is not None and rx.search(text):
            return term
    return None


def contains_any(text: str, needles) -> str | None:
    """First needle appearing as a plain substring. Used for category names,
    which arrive as whole labels ("Kitchen remodeler") rather than free text,
    so substring matching is both safe and what you want."""
    if not text:
        return None
    for n in needles or ():
        if n and n in text:
            return n
    return None
