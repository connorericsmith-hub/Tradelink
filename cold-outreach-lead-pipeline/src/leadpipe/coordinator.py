"""The run loop: pull the queue, scrape, filter, score, store.

Resumable by construction. Job state lives in the database, the phone number
is unique, and nothing here writes export state — so a run that dies halfway
can be restarted with the same command and will pick up where it stopped
without re-scraping what it already has or duplicating a single lead.

The block decision tree is the part worth understanding. Google does not
return an error when it decides it has seen enough of you; it starts serving
fewer results. So:

  failure rate over threshold  -> retry once at lower concurrency, to a
                                  SEPARATE output file so the first attempt's
                                  rows survive, and keep whichever attempt
                                  returned more
  several bad metros in a row  -> stop scraping entirely, write a blocker
                                  file, and surface it

That last step is deliberately not automatic-recovery. The fix is proxies,
proxies cost money, and spending your money is your decision — so the run
stops and tells you rather than quietly continuing to burn the IP.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .categorize import Categorizer
from .classify import Classifier, phone_type_of
from .normalize import load_scraper_records, parse_listing
from .scrape import ScrapeEngine
from .store import Store


class Coordinator:
    def __init__(self, cfg, store: Store, log=print) -> None:
        self.cfg = cfg
        self.store = store
        self.log = log
        self.classifier = Classifier(cfg)
        self.categorizer = Categorizer(cfg)

        self.data_dir = cfg.path_for("storage.data_dir", "data")
        self.raw_dir = self.data_dir / "raw"
        self.engine = ScrapeEngine(cfg, self.data_dir)

        s = cfg.get("scrape") or {}
        self.base_concurrency = int(s.get("concurrency", 4))
        self.batch_size = int(s.get("queries_per_batch", 40))
        self.failure_threshold = float(s.get("failure_rate_threshold", 0.20))
        self.block_streak_limit = int(s.get("block_streak_limit", 3))
        self.thin_threshold = int(s.get("thin_metro_threshold", 15))
        self.blocker_file = self.data_dir / "blocker.txt"

        self.stats = {"raw": 0, "no_phone": 0, "excluded": 0, "denied": 0,
                      "kept": 0, "inserted": 0, "updated": 0}
        self.metro_kept = {}

    # ----------------------------------------------------------------- run

    def run(self, max_batches: int = 0, dry_run: bool = False) -> int:
        ok, message = self.engine.available()
        if not ok:
            self.log("SCRAPER UNAVAILABLE: %s" % message)
            return 2
        self.log("engine: %s" % message)

        if self.blocker_file.exists() and not self.engine.proxies:
            self.log(
                "A previous run hit a global block (%s). Set proxies, or delete "
                "that file to try again from the same IP." % self.blocker_file
            )
            return 3
        if self.blocker_file.exists() and self.engine.proxies:
            self.blocker_file.unlink()
            self.log("proxies configured — clearing the previous block marker")

        concurrency = self.base_concurrency
        block_streak = 0
        batches = 0

        while True:
            pending = self.store.pending_queue()
            if not pending:
                self.log("queue empty")
                break

            head = pending[0]
            # One batch is one metro+pass, so the thin-metro check below can
            # fire the moment a metro's anchor pass completes.
            batch = [
                j for j in pending
                if j["metro"] == head["metro"] and j["pass_no"] == head["pass_no"]
            ][: self.batch_size]

            if dry_run:
                self.log(
                    "DRY RUN: would scrape %d queries for %s pass %d "
                    "(e.g. %r)" % (len(batch), head["metro"], head["pass_no"],
                                   "%s %s" % (batch[0]["keyword"], batch[0]["location"]))
                )
                return 0

            failure_rate, failed_keywords = self._run_batch(batch, concurrency)

            for job in batch:
                status = "error" if job["keyword"] in failed_keywords else "done"
                self.store.set_queue_status([job["id"]], status)

            if failure_rate > self.failure_threshold:
                block_streak += 1
                concurrency = max(1, concurrency - 1)
                self.log(
                    "  elevated failure rate %.2f (streak %d) — concurrency now %d"
                    % (failure_rate, block_streak, concurrency)
                )
                if block_streak >= self.block_streak_limit and not self.engine.proxies:
                    self._surface_block(block_streak)
                    return 3
            else:
                block_streak = 0
                if concurrency < self.base_concurrency:
                    concurrency += 1

            if head["pass_no"] == 1:
                self._check_thin(head)

            progress = self.store.queue_progress()
            self.log(
                "  progress %d/%d jobs | kept %d | new %d"
                % (progress.get("done", 0) + progress.get("error", 0),
                   progress.get("total", 0), self.stats["kept"],
                   self.stats["inserted"])
            )

            batches += 1
            if max_batches and batches >= max_batches:
                self.log("stopping after %d batches (resumable)" % batches)
                break

        self._report()
        return 0

    # --------------------------------------------------------------- batch

    def _run_batch(self, batch, concurrency) -> tuple:
        """Scrape one metro+pass, one scraper invocation per keyword.

        Per keyword rather than per batch so every stored lead records the
        exact keyword that found it — which is the only way to learn that
        three of your twelve keywords are producing all the junk. It also
        isolates failures: one keyword erroring does not lose the others.
        """
        metro, state = batch[0]["metro"], batch[0]["state"]
        by_keyword = {}
        for job in batch:
            by_keyword.setdefault(job["keyword"], []).append(job)

        failed = set()
        finished = errors = 0
        raw_rows = kept_rows = 0

        for keyword, jobs in by_keyword.items():
            out_name = "b_%s.json" % jobs[0]["id"]
            queries = sorted({"%s %s" % (j["keyword"], j["location"]) for j in jobs})
            try:
                result = self.engine.run(queries, out_name, concurrency=concurrency)

                if result["failure_rate"] > self.failure_threshold:
                    retry_conc = max(1, concurrency - 2)
                    self.log(
                        "  %s/%s failure rate %.2f — retrying at concurrency %d"
                        % (metro, keyword, result["failure_rate"], retry_conc)
                    )
                    # Separate output file: the first attempt's rows are real
                    # data and must not be overwritten by a worse retry.
                    retry = self.engine.run(
                        queries, out_name.replace(".json", "_r.json"),
                        concurrency=retry_conc, inactivity="3m",
                    )
                    loser = result["path"] if retry["rows"] >= result["rows"] else retry["path"]
                    if retry["rows"] >= result["rows"]:
                        result = retry
                    self._retain(loser)

                kept = self._ingest(result["path"], metro, state, keyword)
                raw_rows += result["rows"]
                kept_rows += kept
                finished += result["jobs_finished"]
                errors += result["jobs_failed"] + result["blocked_signals"]
                self._retain(result["path"])
            except Exception as e:
                self.log("  %s/%s failed: %s" % (metro, keyword, e))
                failed.add(keyword)
                errors += 1

        rate = round(errors / max(finished + errors, 1), 3)
        self.log(
            "  %s pass %d: %d keywords, raw %d, kept %d, failure rate %.2f"
            % (metro, batch[0]["pass_no"], len(by_keyword), raw_rows, kept_rows, rate)
        )
        return rate, failed

    def _ingest(self, path, metro, state, keyword) -> int:
        """Parse -> filter -> score -> categorize -> store. Returns kept count."""
        records = []
        now = datetime.now(timezone.utc).isoformat()
        for raw in load_scraper_records(path):
            self.stats["raw"] += 1
            lead = parse_listing(raw, metro=metro, state=state)
            if lead is None:
                self.stats["no_phone"] += 1
                continue

            score, flags, verdict = self.classifier.classify(
                lead["business_name"] or "",
                lead["primary_category"] or "",
                lead["categories"],
                review_count=lead["review_count"],
                rating=lead["rating"],
                website=lead["website"],
                phone_type=phone_type_of(lead["phone_e164"]),
            )
            if verdict == "drop":
                key = "excluded" if flags and flags[0].startswith("exclude") else "denied"
                self.stats[key] += 1
                continue

            self.stats["kept"] += 1
            lead.update({
                "score": score,
                "flags": flags,
                "verdict": verdict,
                "source": "gmaps",
                "source_keyword": keyword,
                "scored_at": now,
                "category_value": self.categorizer.value_for(
                    lead["business_name"] or "",
                    lead["primary_category"] or "",
                    lead["categories"],
                ) or None,
            })
            records.append(lead)

        result = self.store.upsert_leads(records, merge=True)
        self.stats["inserted"] += result["inserted"]
        self.stats["updated"] += result["updated"]
        self.metro_kept[metro] = self.metro_kept.get(metro, 0) + len(records)
        return len(records)

    def _retain(self, path) -> None:
        """Keep raw scraper output. Re-scraping to recover it costs a block
        allowance you may not have; keeping it costs disk."""
        src = Path(path)
        if not src.exists():
            return
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dest = self.raw_dir / ("%s_%s" % (stamp, src.name))
        i = 1
        while dest.exists():
            dest = self.raw_dir / ("%s_%s_%d%s" % (stamp, src.stem, i, src.suffix))
            i += 1
        src.replace(dest)

    def _check_thin(self, head) -> None:
        remaining = any(
            j["metro"] == head["metro"] and j["pass_no"] == 1
            for j in self.store.pending_queue()
        )
        if remaining:
            return
        kept = self.metro_kept.get(head["metro"], 0)
        if kept < self.thin_threshold:
            self.log(
                "  THIN METRO: %s kept only %d leads (threshold %d). Usually the "
                "keywords do not match local vocabulary — check what these "
                "businesses call themselves there."
                % (head["metro"], kept, self.thin_threshold)
            )

    def _surface_block(self, streak) -> None:
        message = (
            "GLOBAL BLOCK: the scraper's failure rate stayed above %.0f%% across %d "
            "consecutive metros with no proxies configured. Google is throttling "
            "this IP.\n\n"
            "Scraping is paused. Options:\n"
            "  1. Wait. Soft blocks commonly clear in hours.\n"
            "  2. Configure residential proxies and set the environment variable "
            "named in scrape.proxies_env.\n"
            "  3. Lower scrape.concurrency and scrape.depth and try again.\n\n"
            "Delete this file to resume from the same IP. The queue is unchanged, "
            "so nothing is re-scraped."
            % (self.failure_threshold * 100, streak)
        )
        self.blocker_file.parent.mkdir(parents=True, exist_ok=True)
        self.blocker_file.write_text(message + "\n", encoding="utf-8")
        self.log("\n" + message)

    def _report(self) -> None:
        s = self.stats
        seen = max(s["raw"], 1)
        self.log("\n===== RUN SUMMARY =====")
        self.log("  raw listings seen        %7d" % s["raw"])
        self.log("  no usable phone          %7d" % s["no_phone"])
        self.log("  wrong business model     %7d  (filter.exclude_terms)" % s["excluded"])
        self.log("  off-niche                %7d  (filter.deny_terms)" % s["denied"])
        self.log("  kept                     %7d  (%.1f%% of raw)"
                 % (s["kept"], 100.0 * s["kept"] / seen))
        self.log("  new leads stored         %7d" % s["inserted"])
        self.log("  existing leads enriched  %7d" % s["updated"])
        thin = {m: n for m, n in self.metro_kept.items() if n < self.thin_threshold}
        if thin:
            self.log("  thin metros: %s" % json.dumps(thin))
        self.log("=======================")
