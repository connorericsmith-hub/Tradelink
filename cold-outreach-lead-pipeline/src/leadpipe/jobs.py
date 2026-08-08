"""Build the resumable work queue: every (keyword x location) pair.

Two passes per metro, and the ordering between them is load-bearing:

  pass 1  the metro anchor. Breadth first, so a run interrupted after an hour
          has touched every metro rather than exhausting the first one.
  pass 2  the suburb ring, for metros marked tier 1. Depth second.

Job ids are a hash of (source, keyword, location), which makes seeding
idempotent: re-running the builder after adding a keyword adds only the new
pairs and leaves every completed job alone.
"""
from __future__ import annotations

import hashlib


def job_id(source: str, keyword: str, location: str) -> str:
    raw = "%s|%s|%s" % (source, keyword, location)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def build(cfg) -> list:
    """All queue rows implied by the config. Order matches execution order."""
    keywords = list(cfg.get("scrape.keywords") or [])
    locations = list(cfg.get("scrape.locations") or [])
    rows = []
    for loc in locations:
        metro = str(loc.get("metro") or "").strip()
        state = str(loc.get("state") or "").strip().upper()
        anchor = str(loc.get("anchor") or "").strip() or ("%s %s" % (metro, state))
        rank = int(loc.get("rank", 9999))
        tier = int(loc.get("tier", 2))

        for kw in keywords:
            rows.append(_row("gmaps", kw, anchor, metro, state, rank, tier, 1))

        # Suburbs are pass 2 regardless of tier; tier only controls whether
        # you listed any. Keeping the pass number independent means promoting
        # a metro to tier 1 later just adds jobs, it does not reorder the run.
        for suburb in (loc.get("suburbs") or []):
            for kw in keywords:
                rows.append(
                    _row("gmaps", kw, str(suburb).strip(), metro, state, rank, tier, 2)
                )

    rows.sort(key=lambda r: (r["pass_no"], r["rank"], r["metro"], r["keyword"],
                             r["location"]))
    return rows


def _row(source, keyword, location, metro, state, rank, tier, pass_no) -> dict:
    return {
        "id": job_id(source, keyword, location),
        "source": source,
        "keyword": keyword,
        "location": location,
        "metro": metro,
        "state": state,
        "rank": rank,
        "tier": tier,
        "pass_no": pass_no,
    }


def summarize(rows) -> dict:
    return {
        "total": len(rows),
        "pass1": sum(1 for r in rows if r["pass_no"] == 1),
        "pass2": sum(1 for r in rows if r["pass_no"] == 2),
        "metros": len({r["metro"] for r in rows}),
        "locations": len({r["location"] for r in rows}),
    }
