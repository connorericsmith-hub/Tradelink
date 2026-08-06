"""Permanent suppression list.

A phone in this file is never exported again — whatever its score, whatever
its send_status, however it requalifies after you improve the classifier. It
is a one-way door, and that is the point: the list is the only durable record
that a human at that number has already been contacted, and it has to survive
a database rebuild, a re-scrape, and a rewrite of the scoring rules.

Plain text, one E.164 per line, so it can be read and hand-edited without the
database and without credentials. The exporter appends to it automatically.
"""
from __future__ import annotations

from pathlib import Path

HEADER = (
    "# Permanent suppression list.\n"
    "# Phones already committed to an outreach channel. NEVER export again,\n"
    "# regardless of score, send_status, or requalification.\n"
    "# One E.164 per line. '#' comments and blank lines are ignored.\n"
)


def load(path) -> frozenset:
    p = Path(path)
    if not p.exists():
        return frozenset()
    out = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(line)
    return frozenset(out)


def merge(path, phones) -> tuple:
    """Union new phones into the file. Idempotent. Returns (added, total)."""
    p = Path(path)
    existing = set(load(p))
    before = len(existing)
    merged = existing | {str(x).strip() for x in phones if str(x).strip()}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(HEADER + "\n".join(sorted(merged)) + "\n", encoding="utf-8")
    return len(merged) - before, len(merged)
