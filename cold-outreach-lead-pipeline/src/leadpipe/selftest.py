"""Prove the config before it touches real data.

Seven gates, and the order is deliberate — each one is cheaper to fix than the
one after it:

  1. config validity        every key present, every regex compiles, every
                            referenced value declared
  2. scoring invariant      a bare category match cannot export; a weak-only
                            name cannot export
  3. filter fixtures        listings that must drop, do; listings that must
                            keep, do
  4. categorizer fixtures   the refusals matter more than the assignments
  5. template encoding      the copy in your config is GSM-7 clean
  6. merge-field structure  no sequence can render a hole
  7. quiet hours            the window resolves in a lead's own timezone

Everything runs offline against fixtures. No scraper, no network, no
credentials, no real leads. Run it after every config change:

    leadpipe selftest
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import config as config_mod
from . import gsm7
from .categorize import Categorizer
from .classify import Classifier, phone_type_of
from .messages import MessageRenderer
from .quiet_hours import QuietHours

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "listings.json"


class Result:
    def __init__(self) -> None:
        self.failures = []
        self.warnings = []
        self.lines = []

    def ok(self, msg):
        self.lines.append("  ok    %s" % msg)

    def fail(self, msg):
        self.lines.append("  FAIL  %s" % msg)
        self.failures.append(msg)

    def warn(self, msg):
        self.lines.append("  warn  %s" % msg)
        self.warnings.append(msg)

    def note(self, msg):
        self.lines.append("        %s" % msg)


def run(cfg, fixtures_path=None, log=print) -> int:
    """Return 0 when every gate passes, 1 otherwise."""
    r = Result()
    path = Path(fixtures_path) if fixtures_path else FIXTURES

    log("SELF-TEST — %s" % (cfg.path or "<config>"))
    log("niche: %s\n" % cfg.get("niche.name", "(unnamed)"))

    log("[1/7] config validity")
    problems = config_mod.validate(cfg)
    if problems:
        for p in problems:
            r.fail(p)
    else:
        r.ok("every required key present, every regex compiles, every "
             "referenced value declared")
    _flush(r, log)

    log("[2/7] scoring invariant")
    _check_scoring(cfg, r)
    _flush(r, log)

    fixtures = _load_fixtures(path, r)

    log("[3/7] filter fixtures")
    if fixtures is None:
        r.warn(
            "no fixtures at %s — gates 3 and 4 skipped. Write your own and "
            "pass --fixtures /path/to/listings.json." % path
        )
    else:
        _check_filter(cfg, fixtures, r)
    _flush(r, log)

    log("[4/7] categorizer fixtures")
    if fixtures is None:
        r.warn("no fixtures — skipping")
    else:
        _check_categorize(cfg, fixtures, r)
    _flush(r, log)

    log("[5/7] message template encoding")
    _check_templates(cfg, r)
    _flush(r, log)

    log("[6/7] merge-field completeness")
    _check_merge_fields(cfg, r)
    _flush(r, log)

    log("[7/7] quiet hours")
    _check_quiet_hours(cfg, r)
    _flush(r, log)

    log("")
    if r.failures:
        log("SELF-TEST FAILED — %d problem(s):" % len(r.failures))
        for f in r.failures:
            log("  - %s" % f)
        log("\nFix these before scraping. Nothing here needs the network, so "
            "every one of them is cheaper to fix now than after a run.")
        return 1
    if r.warnings:
        log("SELF-TEST PASSED with %d warning(s)." % len(r.warnings))
    else:
        log("SELF-TEST PASSED. The config is coherent and the fixtures agree "
            "with it.")
    log("\nNext: send the sequence to your own phone before a single real "
        "lead is touched. It is free, and it is the only way to see what "
        "actually lands on a screen.")
    return 0


def _flush(r, log):
    for line in r.lines:
        log(line)
    r.lines = []
    log("")


# ------------------------------------------------------------------- gates

def _check_scoring(cfg, r) -> None:
    report = config_mod.score_report(cfg)
    t = report["threshold"]

    if report["bare_category_exports"]:
        r.fail(
            "a bare core-category match scores %d, at or above the export "
            "threshold of %d — every business your deny list misses that "
            "carries a core category will export"
            % (report["bare_category_score"], t)
        )
    else:
        r.ok("bare category match scores %d < %d, so a category claim alone "
             "cannot export" % (report["bare_category_score"], t))

    if report["bare_category_no_penalty"] >= t:
        r.warn(
            "core_primary_category alone is %d, which already meets the "
            "threshold of %d. It only fails to export because the "
            "no_website_no_reviews penalty applies. A listing with a website "
            "and a core category and nothing else WILL export."
            % (report["bare_category_no_penalty"], t)
        )

    if report["weak_only_exports"]:
        r.fail(
            "a weak-name-only listing scores %d with every soft bonus, at or "
            "above the threshold of %d" % (report["weak_only_score"], t)
        )
    else:
        r.ok("weak-name-only listing tops out at %d < %d even with every soft "
             "bonus" % (report["weak_only_score"], t))


def _check_filter(cfg, fixtures, r) -> None:
    clf = Classifier(cfg)
    dropped = kept = 0

    for row in fixtures.get("drop", []):
        score, flags, verdict = _classify(clf, row, generous=True)
        exportable = verdict != "drop" and score >= clf.threshold
        if verdict != "drop":
            r.fail(
                "must-drop listing survived the filter: %r (%s) -> verdict=%s "
                "score=%d flags=%s | %s"
                % (row["name"], row.get("primary", ""), verdict, score,
                   flags, row.get("why", ""))
            )
        elif exportable:
            r.fail("must-drop listing is exportable: %r" % row["name"])
        else:
            dropped += 1
    if dropped:
        r.ok("%d must-drop listings all rejected, with maximum favourable "
             "soft signals applied" % dropped)

    for row in fixtures.get("keep", []):
        score, flags, verdict = _classify(clf, row, generous=False)
        if verdict != "pass":
            r.fail(
                "must-keep listing did not reach the threshold: %r -> "
                "verdict=%s score=%d (need %d) flags=%s | %s"
                % (row["name"], verdict, score, clf.threshold, flags,
                   row.get("why", ""))
            )
        else:
            kept += 1
    if kept:
        r.ok("%d must-keep listings all cleared the threshold" % kept)

    for row in fixtures.get("low", []):
        score, flags, verdict = _classify(clf, row, generous=False)
        if verdict == "pass":
            r.fail(
                "uncorroborated listing reached the threshold: %r -> score=%d "
                "flags=%s | %s" % (row["name"], score, flags, row.get("why", ""))
            )
    if fixtures.get("low"):
        r.ok("%d uncorroborated listings stored but held below the export bar"
             % len(fixtures["low"]))


def _classify(clf, row, generous: bool):
    """Generous mode gives a must-drop row every favourable soft signal, so a
    rejection proves the structure rejects it rather than the data being thin."""
    reviews = row.get("reviews")
    rating = row.get("rating")
    website = row.get("website")
    if generous:
        reviews = reviews if reviews is not None else 40
        rating = rating if rating is not None else 4.8
        website = website or "https://example.com"
    phone = row.get("phone")
    return clf.classify(
        row.get("name", ""),
        row.get("primary", ""),
        row.get("categories", []),
        review_count=reviews,
        rating=rating,
        website=website,
        phone_type=phone_type_of(phone) if phone else "other",
    )


def _check_categorize(cfg, fixtures, r) -> None:
    cat = Categorizer(cfg)
    cases = fixtures.get("categorize", [])
    if not cases:
        r.warn("no categorizer fixtures")
        return

    refusals = sum(1 for c in cases if not c.get("expect"))
    bad = 0
    for case in cases:
        expected = case.get("expect", "")
        got, reason = cat.derive(
            case.get("name", ""), case.get("primary", ""), case.get("categories", [])
        )
        got = got or ""
        if got != expected:
            bad += 1
            r.fail(
                "categorizer: %r (%s) -> expected %r, got %r (reason: %s) | %s"
                % (case["name"], case.get("primary", ""), expected, got,
                   reason, case.get("why", ""))
            )
    if not bad:
        r.ok("%d categorizer cases pass — %d must-refuse, %d must-assign; no "
             "value is ever guessed" % (len(cases), refusals, len(cases) - refusals))

    declared = set(cfg.get("categorize.values") or [])
    for case in cases:
        got, _ = cat.derive(
            case.get("name", ""), case.get("primary", ""), case.get("categories", [])
        )
        if got and got not in declared:
            r.fail("categorizer produced %r, which is not in categorize.values" % got)


def _check_templates(cfg, r) -> None:
    renderer = MessageRenderer(cfg)
    reports = renderer.qa_templates()
    if not reports:
        r.fail("no message templates configured")
        return

    bad = 0
    for rep in reports:
        where = "sequence %r message %d" % (rep["sequence"], rep["index"])
        if rep["unknown_fields"]:
            bad += 1
            r.fail("%s merges unknown field(s): %s"
                   % (where, ", ".join(rep["unknown_fields"])))
        if rep["other_non_gsm7"]:
            bad += 1
            r.fail(
                "%s contains non-GSM-7 characters that force UCS-2 and roughly "
                "triple the send cost: %s"
                % (where, gsm7.describe(rep["other_non_gsm7"]))
            )
        if rep["emoji"] and not renderer.allow_emoji:
            bad += 1
            r.fail(
                "%s contains emoji (%s). Emoji are never GSM-7. Remove them, or "
                "set messages.encoding.allow_emoji: true to accept UCS-2 rates "
                "on purpose." % (where, gsm7.describe(rep["emoji"]))
            )
        if rep["replaced"]:
            r.note(
                "%s: auto-sanitized %s"
                % (where, ", ".join("%r->%r" % (a, b) for a, b in rep["replaced"]))
            )
        if rep["segments"] > 1:
            r.warn(
                "%s is %d characters — %d %s segments, so you pay %dx per "
                "recipient for this message"
                % (where, rep["length"], rep["segments"], rep["encoding"],
                   rep["segments"])
            )
    if not bad:
        r.ok("%d message templates are GSM-7 clean and merge only declared "
             "fields" % len(reports))


def _check_merge_fields(cfg, r) -> None:
    """A sequence must declare every field it merges, or a lead missing that
    field enters it and renders a blank."""
    renderer = MessageRenderer(cfg)
    guaranteed = config_mod.guaranteed_fields(cfg)
    bad = 0
    for seq in renderer.sequences:
        undeclared = seq.fields_used() - set(seq.requires) - guaranteed
        if undeclared:
            bad += 1
            r.fail(
                "sequence %r merges %s but does not require %s — a lead "
                "missing that field would be sent a message with a hole in it"
                % (seq.name, ", ".join(sorted(seq.fields_used())),
                   ", ".join(sorted(undeclared)))
            )
    if not bad:
        r.ok("every sequence requires each field it merges, so no lead can "
             "receive a message with an empty slot")

    # Prove the routing holds on a lead that is missing everything optional.
    bare = {"phone": "+15555550100", "company": "Example Co", "city": "",
            "state": "", "category_value": ""}
    qa = renderer.qa_lead(bare)
    if qa["ok"]:
        r.ok("a lead with no optional fields routes to %r and renders cleanly"
             % qa["sequence"])
    else:
        r.fail(
            "a lead carrying only a phone and a company name cannot be "
            "messaged: %s. Add a sequence with an empty `requires` list."
            % "; ".join(qa["problems"])
        )

    # And that a fully-populated lead reaches the richer sequence.
    full = dict(bare, city="Springfield", state="TX",
                category_value=(cfg.get("categorize.values") or [""])[0])
    qa_full = renderer.qa_lead(full)
    if qa_full["ok"]:
        r.ok("a fully-populated lead routes to %r" % qa_full["sequence"])
        for m in qa_full["messages"][:1]:
            r.note("preview: %s" % m["body"])
    else:
        r.fail("a fully-populated lead fails QA: %s" % "; ".join(qa_full["problems"]))


def _check_quiet_hours(cfg, r) -> None:
    qh = QuietHours(cfg)
    states = [
        str(loc.get("state") or "").upper()
        for loc in (cfg.get("scrape.locations") or [])
    ]
    states = [s for s in states if s]
    if not states:
        r.warn("no states in scrape.locations — cannot check timezone coverage")
        return

    unmapped = [s for s in states if not qh.timezone_for({"state": s})]
    if unmapped:
        r.fail(
            "no timezone for state(s) %s — leads there cannot be scheduled "
            "safely and quiet hours would silently not apply to them"
            % ", ".join(sorted(set(unmapped)))
        )
    else:
        zones = sorted({qh.timezone_for({"state": s}) for s in states})
        r.ok("every scraped state maps to a timezone (%d distinct: %s)"
             % (len(zones), ", ".join(zones)))

    # Prove the window actually excludes something. A window that allows every
    # hour is a config mistake, not a permissive policy.
    probe = {"state": states[0]}
    early = datetime(2026, 6, 15, 6, 30)
    midday = datetime(2026, 6, 15, 13, 0)
    late = datetime(2026, 6, 15, 23, 30)
    allowed_early, _ = qh.is_open(probe, _localize(qh, probe, early))
    allowed_mid, why_mid = qh.is_open(probe, _localize(qh, probe, midday))
    allowed_late, _ = qh.is_open(probe, _localize(qh, probe, late))

    if allowed_early or allowed_late:
        r.fail(
            "the quiet-hours window admits 06:30 or 23:30 local — check "
            "compliance.quiet_hours.earliest/latest"
        )
    elif not allowed_mid:
        r.fail("the quiet-hours window rejects 13:00 local: %s" % why_mid)
    else:
        r.ok("window %s-%s applies in the lead's own timezone: 06:30 blocked, "
             "13:00 allowed, 23:30 blocked"
             % (qh.earliest.strftime("%H:%M"), qh.latest.strftime("%H:%M")))

    mode = cfg.get("compliance.verification.mode", "none")
    if mode == "none":
        r.warn(
            "compliance.verification.mode is 'none'. Toll-free numbers are "
            "still dropped offline, but landlines will reach your export — "
            "they validate as real numbers, cannot receive SMS, and you are "
            "billed for every attempt."
        )


def _localize(qh, lead, naive):
    """Attach the lead's timezone to a naive local time, for the probe."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(qh.timezone_for(lead))
        return naive.replace(tzinfo=tz)
    except Exception:
        return None


def _load_fixtures(path, r):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return None
    except ValueError as e:
        r.fail("fixtures at %s are not valid JSON: %s" % (path, e))
        return None
    return data
