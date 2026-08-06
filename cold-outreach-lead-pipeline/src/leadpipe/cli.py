"""Command-line entry point.

    leadpipe selftest        prove the config offline, against fixtures
    leadpipe plan            build the work queue from the config
    leadpipe scrape          run the pipeline: scrape, filter, score, store
    leadpipe status          what is in the store
    leadpipe preview         render the sequence for a lead you invent
    leadpipe export          write CRM-ready CSV batches
"""
from __future__ import annotations

import argparse
import sys

from . import jobs, selftest
from .categorize import Categorizer, coverage
from .config import ConfigError, load
from .coordinator import Coordinator
from .export import Exporter
from .messages import MessageRenderer
from .quiet_hours import QuietHours
from .store import Store


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="leadpipe",
        description="Niche lead scraper: scrape, filter, categorize, score, export.",
    )
    parser.add_argument("-c", "--config", help="path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    p_selftest = sub.add_parser(
        "selftest", help="verify the config offline against fixtures"
    )
    p_selftest.add_argument(
        "--fixtures",
        help="path to your own fixture listings JSON. Defaults to the example "
             "fixtures in the repo, which describe the example niche — point "
             "this at your own once you have re-niched the config.",
    )
    sub.add_parser("plan", help="build the work queue from the config")
    sub.add_parser("status", help="show what is in the store")

    p_scrape = sub.add_parser("scrape", help="run the pipeline")
    p_scrape.add_argument("--max-batches", type=int, default=0,
                          help="stop after N batches (resumable)")
    p_scrape.add_argument("--dry-run", action="store_true",
                          help="show the next batch without scraping")

    p_preview = sub.add_parser("preview", help="render the sequence for a made-up lead")
    p_preview.add_argument("--company", default="Example Contracting")
    p_preview.add_argument("--city", default="Springfield")
    p_preview.add_argument("--state", default="TX")
    p_preview.add_argument("--value", default="",
                           help="category value; empty routes to the generic sequence")

    p_export = sub.add_parser("export", help="write CRM-ready CSV batches")
    p_export.add_argument("--dry-run", action="store_true",
                          help="write to a scratch dir; stamp nothing, suppress nothing")

    args = parser.parse_args(argv)

    try:
        cfg = load(args.config)
    except ConfigError as e:
        print("config error: %s" % e, file=sys.stderr)
        return 2

    if args.command == "selftest":
        return selftest.run(cfg, args.fixtures)
    if args.command == "plan":
        return _plan(cfg)
    if args.command == "status":
        return _status(cfg)
    if args.command == "scrape":
        return _scrape(cfg, args)
    if args.command == "preview":
        return _preview(cfg, args)
    if args.command == "export":
        return _export(cfg, args)
    return 2


def _store(cfg) -> Store:
    return Store(cfg.path_for("storage.database", "data/leads.db"))


def _plan(cfg) -> int:
    rows = jobs.build(cfg)
    summary = jobs.summarize(rows)
    with _store(cfg) as store:
        total, added = store.seed_queue(rows)
    print("queue: %d jobs total, %d added now" % (total, added))
    print("  pass 1 (metro anchors): %d" % summary["pass1"])
    print("  pass 2 (suburb rings):  %d" % summary["pass2"])
    print("  %d metros, %d distinct locations, %d keywords"
          % (summary["metros"], summary["locations"],
             len(cfg.get("scrape.keywords") or [])))
    print("\nRe-running this after editing the config adds only new jobs. "
          "Completed work is never reset.")
    return 0


def _scrape(cfg, args) -> int:
    with _store(cfg) as store:
        if not store.pending_queue() and not args.dry_run:
            print("no pending jobs. Run `leadpipe plan` first.")
            return 1
        return Coordinator(cfg, store).run(
            max_batches=args.max_batches, dry_run=args.dry_run
        )


def _status(cfg) -> int:
    min_score = int(cfg.get("score.export_threshold", 50))
    with _store(cfg) as store:
        counts = store.counts(min_score)
        progress = store.queue_progress()
        by_metro = store.by_metro()
        pool = store.exportable(min_score)

        print("STORE  %s" % store.path)
        print("  leads                 %7d" % counts["total"])
        print("  score-qualified >=%-3d %7d" % (min_score, counts["qualified"]))
        print("  unsent                %7d" % counts["pending"])
        print("  exported              %7d" % counts["exported"])
        print("\nQUEUE")
        for status in ("pending", "done", "error"):
            if status in progress:
                print("  %-8s %7d" % (status, progress[status]))
        if by_metro:
            print("\nTOP METROS")
            for metro, n in list(by_metro.items())[:10]:
                print("  %-28s %6d" % (metro, n))

        if pool:
            cov = coverage(
                [{"business_name": r["business_name"],
                  "primary_category": r["primary_category"],
                  "categories": r["categories"]} for r in pool],
                Categorizer(cfg),
            )
            print("\nCATEGORY COVERAGE (exportable pool)")
            print("  %d/%d leads carry a value (%.1f%%)"
                  % (cov["assigned"], cov["total"], 100 * cov["pct"]))
            for value, n in sorted(cov["by_value"].items(), key=lambda kv: -kv[1])[:10]:
                print("    %-28s %6d" % (value or "(none)", n))

            audit = QuietHours(cfg).audit(pool)
            print("\nTIMEZONE SPREAD (exportable pool)")
            for tz, n in list(audit["by_timezone"].items())[:10]:
                print("    %-28s %6d" % (tz, n))
            if audit["unknown"]:
                print("    %-28s %6d  <- cannot be scheduled safely"
                      % "(unknown)", audit["unknown"])
    return 0


def _preview(cfg, args) -> int:
    renderer = MessageRenderer(cfg)
    lead = {
        "phone": "+15555550100",
        "company": args.company,
        "city": args.city,
        "state": args.state,
        "category_value": args.value,
    }
    qa = renderer.qa_lead(lead)
    print("lead: %s, %s %s | %s = %r\n"
          % (lead["company"], lead["city"], lead["state"],
             cfg.get("categorize.field_name", "category_value"),
             lead["category_value"]))
    if qa["sequence"] is None:
        print("matches NO sequence — this lead would be skipped at export.")
        for p in qa["problems"]:
            print("  - %s" % p)
        return 1

    print("routed to sequence: %s\n" % qa["sequence"])
    for m in qa["messages"]:
        print("  [%s] %s, %d chars, %d segment(s)"
              % (m["delay"], m["encoding"], m["length"], m["segments"]))
        print("      %s\n" % m["body"])
    if qa["problems"]:
        print("PROBLEMS — this lead would NOT be exported:")
        for p in qa["problems"]:
            print("  - %s" % p)
        return 1
    print("renders cleanly.")
    return 0


def _export(cfg, args) -> int:
    with _store(cfg) as store:
        Exporter(cfg, store).run(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
