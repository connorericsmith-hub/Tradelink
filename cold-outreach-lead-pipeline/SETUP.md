# Setup — from clone to your first export

Work through this once, top to bottom. Every question below maps to a specific
key in `config.yaml`, named next to it, so you are never guessing where an
answer goes.

Budget about an hour for a niche you know well. Most of that is section 2, and
section 2 is where the value is.

> If you are handing this repo to a coding agent, say: *"Read SETUP.md and
> fill in config.yaml for my niche, then run `leadpipe selftest` until it
> passes."* Answer the questions below first — an agent that invents your
> deny list has invented your results.

---

## 0. Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp config.example.yaml config.yaml
```

You also need a Google Maps CLI scraper. The default is
[gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper),
which emits the full category array per listing:

```bash
# Native (faster). Requires Go.
go install github.com/gosom/google-maps-scraper@latest
# -> installs to ~/go/bin/google-maps-scraper, which is the configured default

# Or Docker (no Go toolchain needed)
docker pull gosom/google-maps-scraper
```

Then set `scrape.engine` to `native`, `docker`, or leave it on `auto`.

Any scraper works as long as it returns **every category** per listing and
accepts a list of queries. If you swap it, `src/leadpipe/scrape.py` is the
only file that changes — it builds the command line and parses the log.

**Check it:**
```bash
leadpipe selftest      # should pass on the shipped example config
```

---

## 1. Your niche

*Config: `niche`, `scrape.keywords`, `scrape.locations`*

- **Business type you are targeting:** `______`

- **Search terms, one per line.** Be specific — "contractor" returns roofers
  and plumbers, "kitchen remodeler" returns kitchen remodelers. 10–15 is
  healthy. Overlap between them is fine; dedupe happens on the phone number.
  ```
  ______
  ```

- **Cities or regions.** For each: the metro name, its 2-letter state, and the
  literal string you'd type into Maps.
  ```
  ______
  ```

- **Suburb rings.** For the metros you care most about, list the surrounding
  towns. These run as a second pass. For most home-services niches the
  affluent ring outside the city limits outperforms the centre, and Maps will
  not surface those businesses from the metro anchor alone.
  ```
  ______
  ```

- **How deep per query?** `scrape.depth`, default 10.
  A single deep pass beats the same city scraped twice shallow — a re-scrape
  mostly re-finds what you already have and costs the same block risk. Go
  deep once.

---

## 2. Who gets dropped

*Config: `filter`*

This section decides your results more than anything else in the file. Spend
the time here.

- **What disqualifies a business even though your search returned it?**
  Wrong trade, wrong service area, too big, franchise, retail rather than a
  contractor. → `filter.deny_terms`
  ```
  ______
  ```

- **What's the wrong business *model* for you?** Kept separate so your run
  report can tell you how much of your yield is the wrong model versus the
  wrong trade — a big number here means your keywords are off.
  Example: if you want project work with a real ticket size, recurring
  maintenance-contract businesses go here. → `filter.exclude_terms`
  ```
  ______
  ```

- **Which of those words only make sense as a category, never in a name?**
  "Church Street Kitchens" is a prospect; a church is not.
  → `filter.category_only_terms`
  ```
  ______
  ```

- **Which are short or ambiguous enough to need a whole-word match?**
  Without this, `pool` drops "Liverpool Kitchens" and `spa` drops anything
  "Spacious". Those are silent false drops — the lead never appears in any
  report. → `filter.whole_word_terms`
  ```
  ______
  ```

- **Review ceiling.** Above this you're looking at a franchise or an
  aggregator that will not read a text from a stranger. Tune to your niche: a
  busy plumber has 800 reviews and is still one truck. → `filter.max_reviews`
  ```
  ______
  ```

Deny terms match on a **leading word boundary**, so `roof` catches roofer,
roofing and roofline in one entry. Every term is checked against the business
name *and* every category on the listing, because the disqualifying label is
often not the first one Maps attached.

---

## 3. Categorizing each lead

*Config: `categorize`*

This is the field your message merges in, so each lead's opener can name its
actual trade.

- **Field name**, exactly as it is keyed in your CRM: `______`
  → `categorize.field_name`

- **Allowed values**, 3–8 of them. Every value must read naturally in your
  message — write each one into the sentence and say it out loud before
  adding it. → `categorize.values`
  ```
  ______
  ```

- **Which values describe the same work at different grain?** Group those into
  families. Inside a family the more specific value wins; across families a
  disagreement is a genuine conflict. → `categorize.families`,
  `categorize.specificity`
  ```
  ______
  ```

- **What happens on a cross-family conflict** — the name says one thing, the
  category says another?
  - `refuse` — assign nothing, route to the generic sequence. Safest.
  - `combined` — name the pair explicitly, e.g. kitchen + bathroom →
    "kitchen and bath remodel". Falls back to refusing when the pair isn't
    listed.

  → `categorize.combine`, `categorize.combined_values`

- **Your veto list.** The most valuable thing in this section and the least
  obvious: words that mean the business is plainly something else, which
  cancel a value even when the name itself suggested one.
  → `categorize.veto_patterns`

  Seed it from your first real export. Skim 200 rows, and every time a value
  makes you wince, the word that caused it goes here.

**Create the field in your CRM before you export**, as a plain text field. If
it doesn't exist, most CRMs accept the import, drop the column, and tell you
nothing.

---

## 4. Your CRM

*Config: `export.columns`*

- **Platform:** `______`
- **Column headers its importer expects:** `______`

The pipeline writes CSV and stops. It does not call your CRM. Map each
internal field (`phone`, `company`, `city`, `state`, `category_value`) to the
header your importer wants, and add rows for any extra field you want carried
through.

If you write your own uploader, keep its credentials in `.env`, never in
`config.yaml` — the config is meant to be shareable and `.env` is gitignored.

---

## 5. The messages

*Config: `messages.sequences`*

- **How many messages, and how far apart?** A common default is 3–5 over
  several days. `______`

Write them with `{field}` placeholders. Then the part that matters:

**Each sequence declares which fields it requires.** A lead is routed to the
*first* sequence whose required fields it actually has. So the normal shape is
two sequences:

```yaml
- name: "personalized"
  requires: ["category_value"]     # only leads that got a value
  messages:
    - { delay: "0m",  body: "... saw your {category_value} work ..." }

- name: "generic"
  requires: ["company"]            # catch-all: company is always present
  messages:
    - { delay: "0m",  body: "... came across {company} ..." }
```

This is a structural split, not a fallback string. A lead with no value never
enters the personalized sequence, so the merge field **cannot** render empty.

Two things the self-test will refuse:
- a sequence that merges a field it doesn't require
- a config with no catch-all, which would silently skip leads

**Write the copy in a plain text editor.** Word processors and notes apps
convert straight quotes to curly ones automatically, and one curly apostrophe
switches the message out of GSM-7 and roughly triples the send cost. The
pipeline substitutes the safe ones and fails QA on the rest, but it is easier
not to introduce them.

**Check it:**
```bash
leadpipe preview                                   # generic sequence
leadpipe preview --value "<one of your values>"    # personalized sequence
```

This prints the real rendered text with character counts and segment counts.
Read it as if you received it.

---

## 6. Phone verification

*Config: `compliance.verification`*

- **Will you verify line type?** `none` / `crm` / `webhook`: `______`

Read this before choosing `none`. A landline comes back "valid" from every
cheap validation API on the market — a real, in-service, correctly-formatted
number that cannot receive a text, and you are billed for every attempt. On a
scraped list of small businesses that is not a rounding error.

- `none` — no check. Toll-free is still dropped offline for free.
- `crm` — your CRM screens at send time. Many do. Confirm yours actually does.
- `webhook` — POST to `LEADPIPE_LINETYPE_URL`, expecting
  `{"line_type": "mobile"|"landline"|"voip"|"toll_free"}`. Fails closed: an
  unverified number is not exported.

---

## 7. Compliance

*Config: `compliance.quiet_hours`*

- **Timezone handling:** `lead_state` (derive per lead) or `fixed`. Use
  `lead_state` unless every lead is in one zone. `______`
- **Send window in the lead's local time:** default 08:00–21:00. `______`

A 9am send from Chicago lands at 6am in Boston. In the US, unsolicited texts
outside roughly 8am–9pm local carry real exposure under the TCPA, with no
general business-to-business exemption. Check your own rules if you are
outside the US.

`leadpipe status` reports the timezone spread of your exportable pool and how
many leads it cannot place at all. If your CRM only supports one global send
window, that report tells you which leads that window handles wrongly.

---

## 8. Prove it, then run it

**a. Adapt the fixtures.** `tests/fixtures/listings.json` holds invented
listings with known right answers, written against the example config. When
you change the niche they will fail — replacing them is how you state what
your filter is supposed to do.

Write the **drop** cases first. The rows you expect to reject teach you more
about your config than the ones you expect to keep. Include at least: a wrong
trade in the name, a wrong trade in a *secondary* category, a retail business,
a franchise above the review ceiling, and a name that looks on-niche but
isn't.

Keep your fixtures wherever you like and point the self-test at them:

```bash
cp tests/fixtures/listings.json ~/my-niche-fixtures.json
# edit it, then:
leadpipe selftest --fixtures ~/my-niche-fixtures.json
```

Until you do, gates 3 and 4 grade your config against the *example* niche and
will report failures that are really just stale fixtures.

**b. Self-test until green.**
```bash
leadpipe selftest --fixtures ~/my-niche-fixtures.json
```
Offline, no scraper, no credentials, no real leads. Every failure it reports
is cheaper to fix now than after a run.

**c. Scrape a little.**
```bash
leadpipe plan
leadpipe scrape --max-batches 1
leadpipe status
```
One batch is one metro's anchor pass. Look at what came back before running
the rest. If the kept rate is very low your keywords are wrong; if it is very
high your deny list is thin.

**d. Dry-run the export and read it.**
```bash
leadpipe export --dry-run
```
Writes to a scratch directory, stamps nothing, suppresses nothing. Open the
CSV. Skim 200 rows. Every value that makes you wince is a veto pattern you're
missing.

**e. Send to yourself.** Import a handful of numbers you control, let the
sequence run, and read what arrives on the screen. Free, and the only way to
see what a stranger actually receives.

**f. Only then, for real.**
```bash
leadpipe scrape
leadpipe export
```

Exported numbers are stamped in the store and appended to the suppression
list, so re-running never produces the same lead twice.

---

## Troubleshooting

**"no config file found"** — you're not in the repo root, or you skipped
`cp config.example.yaml config.yaml`. Or pass `--config /path/to/config.yaml`.

**Self-test says the scoring invariant is broken** — your weights let a bare
category match clear the threshold. Every business your deny list misses that
happens to carry a core category will now export. Raise `export_threshold` or
lower `core_primary_category`.

**Self-test says a value can never be assigned** — you added it to
`categorize.values` but no pattern produces it. Usually a typo between the two
lists.

**Scraper unavailable** — `leadpipe scrape` checks before building anything.
Either the native binary isn't at the configured path or the Docker image
isn't pulled.

**A blocker file appeared and scraping stopped** — Google is throttling your
IP. Wait it out, lower `concurrency` and `depth`, or configure proxies. Delete
the file to resume; the queue is untouched, so nothing is re-scraped.

**A metro came back thin** — usually the keywords don't match local
vocabulary. Search Maps by hand in that city and see what those businesses
call themselves.

**Very few leads carry a category value** — your `name_patterns` are too
narrow, or a veto is over-matching. `leadpipe status` breaks coverage down by
value so you can see which are landing.
