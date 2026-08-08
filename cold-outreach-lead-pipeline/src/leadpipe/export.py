"""Export qualified, unsent leads to CRM-ready CSV batches.

The gauntlet a lead runs before it reaches a file:

  score >= export_threshold      the classifier's bar
  valid E.164                    re-validated here, not trusted from storage
  not suppressed                 hard exclusion, no exceptions
  not already in this run        dedupe by phone
  has a company name             nothing to address otherwise
  line type accepted             per compliance.verification
  passes message QA              every message in its sequence renders with
                                 no empty merge field and no encoding fault

That last one is the gate the others exist to feed. A lead that survives
everything else and would still produce "saw your  work on google" is not
exported — it is reported, with the reason.

Files are staged as `.part` and renamed only after the store has been stamped.
A crash mid-stamp therefore leaves a `.part` file, not an importable CSV full
of leads the database still believes are unsent — which is how the same
thousand people get texted twice.
"""
from __future__ import annotations

import csv
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from . import suppress
from .config import Config
from .messages import MessageRenderer
from .normalize import normalize_phone
from .quiet_hours import QuietHours
from .store import Store


class Exporter:
    def __init__(self, cfg: Config, store: Store, log=print) -> None:
        self.cfg = cfg
        self.store = store
        self.log = log
        self.renderer = MessageRenderer(cfg)
        self.quiet = QuietHours(cfg)

        e = cfg.get("export") or {}
        self.batch_size = int(e.get("batch_size", 1000))
        self.series = e.get("series") or "leads_batch"
        self.columns = list(e.get("columns") or [])
        self.require_company = bool(e.get("require_company_name", True))
        self.out_dir = cfg.path_for("export.output_dir", "out/export")

        self.min_score = int(cfg.get("score.export_threshold", 50))
        self.suppression_file = cfg.path_for(
            "compliance.suppression_file", "data/suppression.txt"
        )
        v = cfg.get("compliance.verification") or {}
        self.verify_mode = v.get("mode", "none")
        self.accept_line_types = set(v.get("accept_line_types") or ["mobile", "voip"])

    # -------------------------------------------------------------- export

    def run(self, dry_run: bool = False) -> dict:
        out_dir = self.out_dir if not dry_run else Path(str(self.out_dir) + "_dryrun")
        out_dir.mkdir(parents=True, exist_ok=True)

        pool = self.store.exportable(self.min_score)
        records, stats, rejects = self.prepare(pool)

        files = []
        start = self._next_index(out_dir)
        for offset in range(0, len(records), self.batch_size):
            index = start + len(files)
            batch = records[offset:offset + self.batch_size]
            path = out_dir / ("%s_%03d.csv" % (self.series, index))
            part = path.with_suffix(".csv.part")
            self._write_csv(part, batch)
            label = "%s_%03d" % (self.series, index)
            if not dry_run:
                self.store.mark_exported([r["phone"] for r in batch], label)
                suppress.merge(self.suppression_file, [r["phone"] for r in batch])
            part.rename(path)
            files.append((path.name, len(batch), label))

        report = {
            "counts": self.store.counts(self.min_score),
            "pool": len(pool),
            "written": len(records),
            "stats": stats,
            "files": files,
            "rejects": rejects,
            "out_dir": str(out_dir),
            "dry_run": dry_run,
        }
        self._print_report(report)
        return report

    def prepare(self, pool) -> tuple:
        """Run the gauntlet. Returns (records, stats, rejects)."""
        suppressed = suppress.load(self.suppression_file)
        stats = dict(bad_phone=0, suppressed=0, duplicate=0, no_company=0,
                     line_type=0, qa_failed=0, no_sequence=0)
        rejects = []
        seen = set()
        out = []

        for row in pool:
            phone = normalize_phone(row.get("phone_e164") or "")
            if not phone:
                stats["bad_phone"] += 1
                continue
            if phone in suppressed:
                stats["suppressed"] += 1
                continue
            if phone in seen:
                stats["duplicate"] += 1
                continue
            seen.add(phone)

            company = (row.get("business_name") or "").strip()
            if self.require_company and not company:
                stats["no_company"] += 1
                continue

            if not self._line_type_ok(phone, row):
                stats["line_type"] += 1
                rejects.append({"phone": phone, "company": company,
                                "reason": "line type not accepted"})
                continue

            lead = {
                "phone": phone,
                "company": company,
                "city": (row.get("city") or "").strip(),
                "state": (row.get("state") or "").strip(),
                "category_value": (row.get("category_value") or "").strip(),
                "score": row.get("score"),
            }

            qa = self.renderer.qa_lead(lead)
            if not qa["ok"]:
                key = "no_sequence" if qa["reason"] == "no_sequence" else "qa_failed"
                stats[key] += 1
                rejects.append({"phone": phone, "company": company,
                                "reason": "; ".join(qa["problems"])})
                continue

            lead["sequence"] = qa["sequence"]
            out.append(lead)

        return out, stats, rejects

    # ------------------------------------------------------------ helpers

    def _line_type_ok(self, phone: str, row: dict) -> bool:
        """Toll-free was already dropped offline at classify time.

        `crm` trusts your CRM to screen at send time — confirm that it
        actually does. `webhook` asks a service you configured.
        """
        if self.verify_mode in ("none", "crm"):
            return True
        known = (row.get("line_type") or "").strip().lower()
        if known:
            return known in self.accept_line_types
        looked_up = self._lookup_line_type(phone)
        if looked_up is None:
            # Fail closed: an unverified number in a mode where you asked for
            # verification is not an export, it is a bill.
            return False
        return looked_up in self.accept_line_types

    def _lookup_line_type(self, phone: str):
        url = os.environ.get("LEADPIPE_LINETYPE_URL")
        if not url:
            return None
        body = json.dumps({"phone": phone}).encode()
        headers = {"Content-Type": "application/json"}
        token = os.environ.get("LEADPIPE_LINETYPE_TOKEN")
        if token:
            headers["Authorization"] = "Bearer %s" % token
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode() or "{}")
        except (urllib.error.URLError, ValueError, OSError) as e:
            self.log("  line-type lookup failed for %s: %s" % (phone, e))
            return None
        return str(data.get("line_type") or "").strip().lower() or None

    def _write_csv(self, path: Path, records) -> None:
        headers = [c.get("header") for c in self.columns]
        sources = [c.get("source") for c in self.columns]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(headers)
            for r in records:
                w.writerow([r.get(s, "") for s in sources])

    def _next_index(self, out_dir: Path) -> int:
        """Next sequential batch number, counting leftover .part files so a
        partially-stamped number is never reused."""
        pattern = re.compile(r"%s_(\d+)\.csv(\.part)?$" % re.escape(self.series))
        used = []
        for f in out_dir.glob("%s_*.csv*" % self.series):
            m = pattern.fullmatch(f.name)
            if m:
                used.append(int(m.group(1)))
        return (max(used) + 1) if used else 1

    def _print_report(self, r) -> None:
        s, c = r["stats"], r["counts"]
        mode = "DRY RUN (nothing stamped, nothing suppressed)" if r["dry_run"] else "live"
        self.log("\n===== EXPORT FUNNEL [%s] =====" % mode)
        self.log("  leads in store               %7d" % c["total"])
        self.log("  score-qualified (>=%-3d)      %7d" % (self.min_score, c["qualified"]))
        self.log("  of those, unsent             %7d" % r["pool"])
        self.log("  - unparseable phone          %7d" % s["bad_phone"])
        self.log("  - suppressed                 %7d" % s["suppressed"])
        self.log("  - duplicate phone            %7d" % s["duplicate"])
        self.log("  - no company name            %7d" % s["no_company"])
        self.log("  - line type rejected         %7d" % s["line_type"])
        self.log("  - failed message QA          %7d" % s["qa_failed"])
        self.log("  - matched no sequence        %7d" % s["no_sequence"])
        self.log("  written                      %7d" % r["written"])

        by_seq = {}
        for f in r["files"]:
            by_seq[f[0]] = f[1]
        if r["files"]:
            self.log("  files                        %7d  -> %s"
                     % (len(r["files"]), r["out_dir"]))
            for name, n, label in r["files"]:
                self.log("      %s  %5d rows  (send_status=%s)" % (name, n, label))

        if r["rejects"]:
            self.log("\n  first rejected leads (fix these, then re-run):")
            for rec in r["rejects"][:5]:
                self.log("      %s %s: %s" % (rec["phone"], rec["company"], rec["reason"]))
            if len(r["rejects"]) > 5:
                self.log("      ... and %d more" % (len(r["rejects"]) - 5))
        self.log("=" * 38)
