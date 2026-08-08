# cold-outreach-lead-pipeline

Find local businesses in your niche on Google Maps, throw away the ones that
don't fit, work out what each survivor actually does, and hand your CRM a
clean CSV that is ready to message.

Everything niche-specific lives in **one YAML file**. No keyword, deny term,
score weight, category value, or word of message copy appears anywhere in the
source. Point it at a different trade by editing config, not Python.

```
scrape ──▶ filter ──▶ normalize ──▶ categorize ──▶ score ──▶ export
```

---

## What it actually does

**1. Scrape.** Runs your keywords against Google Maps, once per location, via
an external CLI scraper. Pulls name, phone, address, rating, review count,
website, and **every category tag** on the listing — Maps attaches up to ten,
and the rest of the pipeline needs all of them.

**2. Filter — deny-first.** Every deny rule is checked against the business
name and against every category, and one hit is fatal before any score is
computed.

> This ordering is the whole design. A plumbing company with sixty five-star
> reviews and a good website out-scores a real prospect that has none of those
> things. If score decides first, the plumber ships. No amount of quality
> makes a business in the wrong trade worth a message.

**3. Normalize.** Every phone becomes E.164 or is discarded, and lands in a
local SQLite store keyed on that number. Re-scraping a city you already did
enriches what you have instead of duplicating it.

**4. Categorize.** Works out the one thing each lead does — "kitchen remodel",
"cabinet", "tile" — so the opener can say *saw your cabinet work* instead of
*saw your work*. Corroboration-first, and it refuses when unsure.

> Naming a trade the operator does **not** do is worse than naming none. It
> proves you are a bot on the first line, to someone who was already
> suspicious. Category data is dirty in a specific way: Maps will label a
> marine contractor and a boat detailer both "Deck builder". The business name
> is the more reliable witness, and when the two disagree the pipeline assigns
> nothing and routes the lead to a sequence that doesn't need a value.

**5. Score.** Survivors accumulate points; a threshold gates the export. One
invariant is enforced and machine-checked: **a bare category match can never
export.** Being listed under "Kitchen remodeler" is a claim Maps made, not
evidence — plenty of the businesses you just denied carry it too. At least one
independent signal (a name that says what they do, real reviews, a website)
has to corroborate.

**6. Export + QA.** Writes CRM-ready CSV batches. Before a lead reaches a file
the pipeline renders **every message it would receive** and refuses the lead
if any merge field is empty or any character would break GSM-7 encoding.

The pipeline **does not send**. It stops at a CSV, because every CRM's import
and automation API is different and a scraper that also owns your sending is a
scraper you can't swap out.

---

## Install

Requires **Python 3.9+** and a Google Maps CLI scraper.

```bash
git clone https://github.com/user3737368/cold-outreach-lead-pipeline.git
cd cold-outreach-lead-pipeline

python3 -m venv .venv && source .venv/bin/activate
pip install -e .

cp config.example.yaml config.yaml
```

Then install the scraper — see **[SETUP.md](SETUP.md)** for the one-liner and
for the full fill-in-the-blanks walkthrough.

## Quickstart

```bash
leadpipe selftest            # prove your config offline. Do this first.
leadpipe plan                # build the work queue from the config
leadpipe scrape --dry-run    # show the next batch without touching Google
leadpipe scrape              # run it
leadpipe status              # what you have, and how it breaks down
leadpipe preview             # see the actual messages, rendered
leadpipe export --dry-run    # build the CSVs without committing anything
leadpipe export              # write them and stamp the store
```

`leadpipe selftest` needs no network, no scraper, no credentials and no real
leads. It runs against fixtures and will tell you your config is wrong before
you spend a scrape finding out. Run it after every config change.

```
[1/7] config validity          every key present, every regex compiles
[2/7] scoring invariant        a bare category match cannot export
[3/7] filter fixtures          listings that must drop, do
[4/7] categorizer fixtures     the refusals matter more than the assignments
[5/7] template encoding        your copy is GSM-7 clean
[6/7] merge-field completeness no sequence can render a hole
[7/7] quiet hours              the window resolves in the lead's timezone
```

Every command is resumable. Job state lives in the database and the phone
number is unique, so a run that dies halfway restarts with the same command
and picks up where it stopped.

---

## Config reference

One file, eight sections. `config.example.yaml` is a worked example for a
generic home-services niche (kitchen & bath remodeling) and is commented
throughout with *why*, not just *what*.

| Section | What it controls |
|---|---|
| `niche` | A label for logs and filenames. |
| `scrape` | Keywords, locations and suburb rings, scraper engine, depth, concurrency, block-detection thresholds. |
| `filter` | `exclude_terms` (wrong business model) and `deny_terms` (wrong trade entirely), `category_only_terms`, `whole_word_terms`, `primary_category_deny`, review ceiling, toll-free drop. |
| `score` | `export_threshold`, every point weight, core categories, strong and weak name signals, review band, rating threshold. |
| `categorize` | The CRM field name and its allowed values, name/category patterns, families, specificity order, the veto list, and how conflicts combine. |
| `export` | Batch size, filename series, output dir, CSV columns. |
| `messages` | Merge fields, the sequences, and encoding policy. |
| `compliance` | Quiet hours and the state→timezone map, line-type verification, suppression file. |

Three details worth knowing before you edit:

**Deny terms are prefixes.** `roof` catches roofer, roofing and roofline in
one term — trades suffix their own name and you won't think of every ending.
Terms in `whole_word_terms` require a full word match on both sides, which is
what stops `pool` from dropping "Liverpool Kitchens".

**`category_only_terms` exist because names steal words.** "Church Street
Kitchens" is a prospect; a church is not. Those terms are checked against the
listing's categories only, never its name.

**Sequences are tried in order, and the split is structural.** Each lead takes
the first sequence whose `requires` fields it actually has. A lead with no
category value never enters the personalized sequence, so the merge field
cannot render empty. This is not a fallback string — it is a different
sequence.

---

## The hard rules

These are baked in. Most of them are here because the failure is silent.

**No characters outside GSM-7 in your copy.** One curly apostrophe switches
the entire message from 160 characters per segment to 70, roughly tripling
what you pay to send it. They arrive when copy is written in a word processor
that converts quotes and dashes automatically, and they are invisible on
screen. The pipeline auto-substitutes the safe ones, reports the rest with
their code points, and fails QA on anything left. Emoji are never GSM-7 —
`allow_emoji: true` makes paying UCS-2 rates for tone a deliberate choice
rather than an accident.

**A lead missing a merge field gets skipped, not blanked.** Never a message
with a hole in it, never a fallback word swapped in. The CRM will accept the
contact, the automation will fire, and the text will read *saw your  work on
google* with two spaces. Nothing errors. You find out from the replies.

**Verify line type before sending, not just validity.** A landline comes back
"valid" from every cheap validation API on the market. It is a real,
correctly-formatted, in-service number that simply cannot receive a text — and
you are billed for every attempt. On a scraped list of small businesses,
landlines are not a rounding error. Toll-free numbers are dropped offline for
free; landline detection needs a provider, and `mode: webhook` fails closed
rather than sending anyway.

**Quiet hours in the LEAD's timezone.** A 9am send from Chicago lands at 6am
in Boston. In the US, unsolicited texts outside roughly 8am–9pm local carry
real exposure under the TCPA, with no general business-to-business
exemption — a small contractor's business line is very often a personal
mobile. Check your own jurisdiction if you are outside the US. `leadpipe
status` shows the timezone spread of your exportable pool, including how many
leads you cannot place at all.

**Scrape each city once, deeply.** A single deep pass returns meaningfully
more real businesses than the same city scraped twice shallow. A re-scrape
mostly re-finds what you already have and costs you the same block risk for a
fraction of the yield. Set `depth` and go once.

**Test on your own numbers first.** Before a single real lead is touched, run
the sequence to a phone you control and read what lands on the screen. It is
free, and it is the only way to see what a stranger actually receives.

**When Google blocks you, the pipeline stops and tells you.** It does not
quietly keep burning the IP. Failure rate over threshold retries once at lower
concurrency to a separate output file; several bad metros in a row pauses the
run and writes a blocker file. The fix is proxies, proxies cost money, and
spending your money is your decision.

---

## Development

```bash
pip install -e ".[dev]"
pytest
```

The test suite runs offline in under a second. `tests/fixtures/listings.json`
holds invented listings with known right answers; adapt them when you adapt
the config, and **write the drops first** — the rows you expect to reject
teach you more about your filter than the ones you expect to keep.

## Legal

You are responsible for how you use this. Scraping, contact-data handling, and
unsolicited messaging are all regulated, and the rules differ by
jurisdiction — TCPA and state mini-TCPAs in the US, GDPR/PECR in the
EU and UK, CASL in Canada, among others. Read Google's terms. Read your
carrier's and CRM's acceptable-use policy. Honour opt-outs immediately and
permanently; the suppression list is there for exactly that.

This tool prepares data. What you send, to whom, and when is on you.

## License

MIT — see [LICENSE](LICENSE).
