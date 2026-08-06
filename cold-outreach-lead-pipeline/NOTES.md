# Setup notes — cold-outreach-lead-pipeline

Vendored from https://github.com/user3737368/cold-outreach-lead-pipeline
(upstream, MIT licensed) and configured for our niche.

## What's here

- `config.yaml` — our real config: HVAC, plumbing, roofing, electrical
  (primary) + landscaping, garage door, pest control, pressure washing,
  handyman (secondary), across 20 metros. **No existing website is a hard
  requirement**, not just a scoring bonus — see the patch below.
- `tests/fixtures/listings.json` — replaces upstream's example fixtures with
  ones written against our own filter/categorize rules. `leadpipe selftest`
  passes clean against this config + these fixtures.
- `src/leadpipe/classify.py` — small patch on top of upstream: added
  `filter.require_no_website`. Upstream only scored "has a website" as a
  soft signal; our offer (building a site) needed it as a hard, deny-first
  exclusion. Search the file for `require_no_website` to see the change —
  it's about 8 lines, isolated to the deny stage.

## Known gap: the scraper needs a real network

`leadpipe scrape` shells out to `gosom/google-maps-scraper`, which downloads
its own headless Chromium (a "Chrome for Testing" build) on first run. That
download was blocked by the sandboxed session's egress policy while this was
being set up, so **the scrape itself has not been run for real yet** —
everything up through `leadpipe plan` / `leadpipe scrape --dry-run` is
verified working, but no live batch has completed.

To actually scrape, run this from a machine with normal internet access:

```bash
go install github.com/gosom/google-maps-scraper@latest
cd cold-outreach-lead-pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
leadpipe selftest        # should pass immediately, config is already built
leadpipe plan
leadpipe scrape --max-batches 1   # test on one metro (Atlanta first) before running all 20
```

See `SETUP.md` for the full walkthrough and `README.md` for how the
pipeline works. Do NOT run all 20 metros in one pass — see the
concurrency/block-detection notes in `config.yaml`'s scrape section; batch
it 2-4 metros at a time and watch `leadpipe status` between batches.

## Still open

- CRM: export columns are mapped to GoHighLevel's standard import headers
  (Business Name / Phone / Address / City / State) plus a custom `trade_type`
  field. No First Name / Email columns — Google Maps listings don't carry
  either, and nothing here fabricates a merge field it can't back with real
  data.
- Line-type verification is set to `mode: "crm"`, which is correct for
  GoHighLevel's "Number Intelligence" feature — but only if it's actually
  turned on in the GHL workflow. Confirm before relying on it.
- Deny/exclude lists and `max_reviews` are a first pass; tune after skimming
  your first real export, per SETUP.md section 2.
