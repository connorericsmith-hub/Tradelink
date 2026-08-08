"""Local SQLite lead store.

`phone_e164` is UNIQUE. That one constraint is what makes the whole pipeline
safely re-runnable: re-scraping a city you already did enriches the rows you
have instead of creating a second copy of every lead.

Two upsert modes, and the difference matters:

  merge=True   a fresh scrape of a source that knows everything. Non-null
               fields overwrite; nulls do not, so a re-scrape that happens to
               be missing a website never erases the one you already had.
  merge=False  a source that only knows name and phone. Insert-only, so a
               thin source cannot clobber a rich record.

`send_status` is never written by either. Export state is owned by the
exporter alone — a re-scrape must not be able to move a lead you already
messaged back into the unsent pool.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_e164        TEXT    NOT NULL UNIQUE,
    phone_raw         TEXT,
    business_name     TEXT,
    primary_category  TEXT,
    categories        TEXT,          -- JSON array
    rating            REAL,
    review_count      INTEGER,
    website           TEXT,
    address           TEXT,
    city              TEXT,
    state             TEXT,
    metro             TEXT,
    source            TEXT,
    source_keyword    TEXT,
    score             INTEGER,
    flags             TEXT,          -- JSON array
    verdict           TEXT,
    category_value    TEXT,
    line_type         TEXT,
    send_status       TEXT NOT NULL DEFAULT 'pending',
    scored_at         TEXT,
    created_at        TEXT,
    updated_at        TEXT
);
CREATE INDEX IF NOT EXISTS leads_send_status_idx ON leads (send_status);
CREATE INDEX IF NOT EXISTS leads_score_idx       ON leads (send_status, score DESC);
CREATE INDEX IF NOT EXISTS leads_metro_idx       ON leads (metro);

CREATE TABLE IF NOT EXISTS queue (
    id       TEXT PRIMARY KEY,
    source   TEXT,
    keyword  TEXT,
    location TEXT,
    metro    TEXT,
    state    TEXT,
    rank     INTEGER,
    tier     INTEGER,
    pass_no  INTEGER,
    status   TEXT NOT NULL DEFAULT 'pending',
    kept     INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS queue_status_idx ON queue (status, pass_no, rank);
"""

# Columns a scrape may write. `send_status` is deliberately absent.
_WRITABLE = (
    "phone_raw", "business_name", "primary_category", "categories", "rating",
    "review_count", "website", "address", "city", "state", "metro", "source",
    "source_keyword", "score", "flags", "verdict", "category_value", "line_type",
    "scored_at", "updated_at",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------- leads

    def upsert_leads(self, records, merge: bool = True) -> dict:
        """Insert or enrich. Returns {'inserted': n, 'updated': n}."""
        now = _now()
        inserted = updated = 0
        cur = self.conn.cursor()
        seen = set()
        for rec in records:
            phone = rec.get("phone_e164")
            if not phone or phone in seen:
                continue  # dedupe within the batch as well as against the table
            seen.add(phone)

            row = {k: rec.get(k) for k in _WRITABLE if k in rec}
            # The JSON array columns need the same absent-means-absent
            # treatment as everything else, and it has to be done by hand
            # because json.dumps([]) is the non-null string "[]" — which would
            # sail through the merge filter below and overwrite a real array
            # with an empty one. An empty array is treated as absent for the
            # same reason: a scraped card with no category chips means "this
            # pass saw no categories", not "this business has none".
            for key in ("categories", "flags"):
                if rec.get(key):
                    row[key] = json.dumps(rec[key])
                else:
                    row.pop(key, None)
            row["updated_at"] = now
            row["scored_at"] = rec.get("scored_at") or now

            exists = cur.execute(
                "SELECT id FROM leads WHERE phone_e164 = ?", (phone,)
            ).fetchone()

            if exists is None:
                cols = ["phone_e164", "created_at"] + list(row.keys())
                vals = [phone, now] + list(row.values())
                cur.execute(
                    "INSERT INTO leads (%s) VALUES (%s)"
                    % (", ".join(cols), ", ".join("?" * len(cols))),
                    vals,
                )
                inserted += 1
            elif merge:
                # Only non-null values overwrite: a scrape missing a field
                # must not erase a value an earlier scrape found.
                sets = {k: v for k, v in row.items() if v is not None}
                if sets:
                    cur.execute(
                        "UPDATE leads SET %s WHERE phone_e164 = ?"
                        % ", ".join("%s = ?" % k for k in sets),
                        list(sets.values()) + [phone],
                    )
                    updated += 1
        self.conn.commit()
        return {"inserted": inserted, "updated": updated}

    def exportable(self, min_score: int) -> list:
        """Unsent leads at or above the threshold, best first."""
        rows = self.conn.execute(
            "SELECT * FROM leads WHERE send_status = 'pending' AND score >= ? "
            "ORDER BY score DESC, id ASC",
            (min_score,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def mark_exported(self, phones, label: str) -> int:
        cur = self.conn.cursor()
        cur.executemany(
            "UPDATE leads SET send_status = ?, updated_at = ? WHERE phone_e164 = ?",
            [(label, _now(), p) for p in phones],
        )
        self.conn.commit()
        return cur.rowcount

    def counts(self, min_score: int) -> dict:
        q = self.conn.execute
        return {
            "total": q("SELECT COUNT(*) FROM leads").fetchone()[0],
            "qualified": q(
                "SELECT COUNT(*) FROM leads WHERE score >= ?", (min_score,)
            ).fetchone()[0],
            "pending": q(
                "SELECT COUNT(*) FROM leads WHERE send_status = 'pending'"
            ).fetchone()[0],
            "exported": q(
                "SELECT COUNT(*) FROM leads WHERE send_status != 'pending'"
            ).fetchone()[0],
        }

    def by_metro(self) -> dict:
        rows = self.conn.execute(
            "SELECT metro, COUNT(*) n FROM leads GROUP BY metro ORDER BY n DESC"
        ).fetchall()
        return {(r["metro"] or "?"): r["n"] for r in rows}

    @staticmethod
    def _row(r) -> dict:
        d = dict(r)
        for key in ("categories", "flags"):
            try:
                d[key] = json.loads(d.get(key) or "[]")
            except (TypeError, ValueError):
                d[key] = []
        return d

    # ------------------------------------------------------------- queue

    def seed_queue(self, rows) -> tuple:
        """Add queue rows that are not already present.

        Never resets an existing row's status — that is what makes a resumed
        run pick up where it stopped instead of re-scraping everything.
        """
        cur = self.conn.cursor()
        added = 0
        for r in rows:
            existing = cur.execute(
                "SELECT id FROM queue WHERE id = ?", (r["id"],)
            ).fetchone()
            if existing:
                continue
            cur.execute(
                "INSERT INTO queue (id, source, keyword, location, metro, state, "
                "rank, tier, pass_no, status) VALUES (?,?,?,?,?,?,?,?,?,'pending')",
                (r["id"], r["source"], r["keyword"], r["location"], r["metro"],
                 r["state"], r["rank"], r["tier"], r["pass_no"]),
            )
            added += 1
        self.conn.commit()
        total = cur.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        return total, added

    def pending_queue(self) -> list:
        """Pending work, ordered so every pass-1 anchor runs before any pass-2
        suburb, and higher-priority metros run first within a pass."""
        rows = self.conn.execute(
            "SELECT * FROM queue WHERE status = 'pending' "
            "ORDER BY pass_no ASC, rank ASC, metro ASC, keyword ASC, location ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def set_queue_status(self, ids, status: str, kept: int = 0) -> None:
        self.conn.executemany(
            "UPDATE queue SET status = ?, kept = kept + ? WHERE id = ?",
            [(status, kept, i) for i in ids],
        )
        self.conn.commit()

    def queue_progress(self) -> dict:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) n FROM queue GROUP BY status"
        ).fetchall()
        out = {r["status"]: r["n"] for r in rows}
        out["total"] = sum(out.values())
        return out
