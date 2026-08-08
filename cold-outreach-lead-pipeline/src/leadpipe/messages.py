"""Message rendering and the QA that runs before any lead is exported.

Two rules, both structural rather than advisory:

  1. MERGE-FIELD COMPLETENESS. A lead is only routed to a sequence whose
     required fields it actually has. It is not sent a message with a blank
     in it, and it is not sent a message with a fallback word swapped in
     either. Sequences are tried in config order and the lead takes the first
     one it qualifies for — so a lead with no category value never enters the
     personalized sequence at all, and the merge field cannot render empty.

     This is worth being pedantic about because the failure is silent. The
     CRM accepts the contact, the automation fires, and the message goes out
     reading "saw your  work on google" — two spaces where the trade should
     be. Nothing errors. You find out from the replies.

  2. ENCODING. Every rendered body is checked against GSM-7. See gsm7.py for
     why one invisible character triples the send cost.
"""
from __future__ import annotations

import re

from . import gsm7
from .config import Config

PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")


class Sequence:
    def __init__(self, spec: dict) -> None:
        self.name = spec.get("name") or "unnamed"
        self.requires = list(spec.get("requires") or [])
        self.messages = list(spec.get("messages") or [])

    def fields_used(self) -> set:
        used = set()
        for m in self.messages:
            used |= set(PLACEHOLDER_RE.findall((m or {}).get("body") or ""))
        return used


class MessageRenderer:
    def __init__(self, cfg: Config) -> None:
        m = cfg.get("messages") or {}
        self.merge_fields = list(m.get("merge_fields") or [])
        self.sequences = [Sequence(s) for s in (m.get("sequences") or [])]
        enc = m.get("encoding") or {}
        self.enforce_gsm7 = bool(enc.get("enforce_gsm7", True))
        self.auto_sanitize = bool(enc.get("auto_sanitize", True))
        self.allow_emoji = bool(enc.get("allow_emoji", False))

    # ------------------------------------------------------------- routing

    def route(self, lead: dict):
        """First sequence whose required fields the lead actually has.

        None means the lead qualifies for nothing and must not be exported.
        With a catch-all sequence configured (empty `requires`) this cannot
        happen; the config validator warns when there isn't one.
        """
        for seq in self.sequences:
            if all(_present(lead, f) for f in seq.requires):
                return seq
        return None

    # ------------------------------------------------------------- render

    def render(self, body: str, lead: dict) -> tuple:
        """Substitute placeholders. Returns (text, missing_fields).

        A missing field is left as its literal placeholder rather than being
        blanked, so a QA failure is visible in the output instead of looking
        like a slightly short sentence.
        """
        missing = []

        def repl(match):
            field = match.group(1)
            value = _value(lead, field)
            if not value:
                missing.append(field)
                return match.group(0)
            return value

        return PLACEHOLDER_RE.sub(repl, body or ""), missing

    def qa_lead(self, lead: dict) -> dict:
        """Render every message of the lead's sequence and report problems.

        `ok` False means do not export this lead.
        """
        seq = self.route(lead)
        if seq is None:
            return {
                "ok": False,
                "sequence": None,
                "reason": "no_sequence",
                "problems": ["matches no sequence: missing required merge fields"],
                "messages": [],
            }

        problems = []
        rendered = []
        for i, msg in enumerate(seq.messages):
            body, missing = self.render((msg or {}).get("body") or "", lead)
            for field in missing:
                problems.append(
                    "message %d merges {%s}, which is empty for this lead" % (i, field)
                )
            enc = gsm7.check(body, self.auto_sanitize, self.allow_emoji)
            if self.enforce_gsm7 and not enc["ok"]:
                if enc["other_non_gsm7"]:
                    problems.append(
                        "message %d contains non-GSM-7 characters: %s"
                        % (i, gsm7.describe(enc["other_non_gsm7"]))
                    )
                if enc["emoji"] and not self.allow_emoji:
                    problems.append(
                        "message %d contains emoji (%s), which force UCS-2. Set "
                        "messages.encoding.allow_emoji: true to accept the cost."
                        % (i, gsm7.describe(enc["emoji"]))
                    )
            rendered.append({
                "index": i,
                "delay": (msg or {}).get("delay"),
                "body": enc["body"],
                "encoding": enc["encoding"],
                "segments": enc["segments"],
                "length": enc["length"],
                "replaced": enc["replaced"],
                "missing_fields": missing,
            })

        return {
            "ok": not problems,
            "sequence": seq.name,
            "reason": "ok" if not problems else "qa_failed",
            "problems": problems,
            "messages": rendered,
        }

    def qa_templates(self) -> list:
        """Encoding check on the raw copy itself, before any lead is involved.

        Catches the curly quote your word processor inserted, in the config,
        without needing a single scraped lead. This is the check to run right
        after pasting new copy.
        """
        out = []
        for seq in self.sequences:
            for i, msg in enumerate(seq.messages):
                body = (msg or {}).get("body") or ""
                enc = gsm7.check(body, self.auto_sanitize, self.allow_emoji)
                out.append({
                    "sequence": seq.name,
                    "index": i,
                    "ok": enc["ok"] or not self.enforce_gsm7,
                    "encoding": enc["encoding"],
                    "segments": enc["segments"],
                    "length": enc["length"],
                    "replaced": enc["replaced"],
                    "emoji": enc["emoji"],
                    "other_non_gsm7": enc["other_non_gsm7"],
                    "unknown_fields": sorted(
                        set(PLACEHOLDER_RE.findall(body)) - set(self.merge_fields)
                    ),
                })
        return out


def _value(lead: dict, field: str) -> str:
    return str(lead.get(field) or "").strip()


def _present(lead: dict, field: str) -> bool:
    return bool(_value(lead, field))
