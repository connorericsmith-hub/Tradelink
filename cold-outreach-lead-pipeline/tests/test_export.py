"""Store and export. Every phone here is in the reserved 212-555-01XX fictional range."""
import csv

import pytest

from leadpipe import suppress
from leadpipe.export import Exporter
from leadpipe.store import Store


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "leads.db") as s:
        yield s


@pytest.fixture
def wired(cfg, tmp_path):
    """Config pointed at a temp dir so nothing touches the working tree."""
    cfg.path = tmp_path / "config.yaml"
    cfg.data["export"]["output_dir"] = "out"
    cfg.data["compliance"]["suppression_file"] = "suppression.txt"
    return cfg


def _lead(n, **kw):
    base = {
        "phone_e164": "+1212555%04d" % (100 + n),
        "business_name": "Example Co %d" % n,
        "primary_category": "Kitchen remodeler",
        "categories": ["Kitchen remodeler"],
        "city": "Springfield", "state": "TX",
        "score": 75, "verdict": "pass", "flags": ["core_primary:kitchen remodeler"],
        "category_value": "kitchen remodel",
    }
    base.update(kw)
    return base


# ------------------------------------------------------------------- store

def test_phone_uniqueness_makes_rescrapes_additive(store):
    assert store.upsert_leads([_lead(1)])["inserted"] == 1
    result = store.upsert_leads([_lead(1)])
    assert result["inserted"] == 0 and result["updated"] == 1
    assert store.counts(50)["total"] == 1


def test_merge_never_nulls_an_existing_value(store):
    store.upsert_leads([_lead(1, website="https://example.com")])
    store.upsert_leads([_lead(1, website=None)])
    row = store.exportable(50)[0]
    assert row["website"] == "https://example.com"


def test_merge_never_wipes_a_stored_array(store):
    """The JSON columns need the same absent-means-absent rule as the rest.

    json.dumps([]) is the non-null string "[]", so a thinner second pass used
    to sail through the merge filter and erase the category array a richer
    first pass had found — silently, and the classifier's deny stage depends
    on that array.
    """
    store.upsert_leads([_lead(1, categories=["Kitchen remodeler", "Cabinet maker"],
                              flags=["core_primary:kitchen remodeler"])])

    # A later pass whose card carried no category chips at all.
    store.upsert_leads([_lead(1, categories=[], flags=[])])
    assert store.exportable(50)[0]["categories"] == ["Kitchen remodeler", "Cabinet maker"]

    # And a record that omits the keys entirely.
    thin = _lead(1)
    del thin["categories"]
    store.upsert_leads([thin])
    row = store.exportable(50)[0]
    assert row["categories"] == ["Kitchen remodeler", "Cabinet maker"]
    assert row["flags"] == ["core_primary:kitchen remodeler"]


def test_merge_still_updates_an_array_that_grew(store):
    """Absent must not overwrite, but a real new value must."""
    store.upsert_leads([_lead(1, categories=["Kitchen remodeler"])])
    store.upsert_leads([_lead(1, categories=["Kitchen remodeler", "Tile contractor"])])
    assert store.exportable(50)[0]["categories"] == [
        "Kitchen remodeler", "Tile contractor"
    ]


def test_insert_only_mode_does_not_clobber(store):
    store.upsert_leads([_lead(1, business_name="Rich Record")])
    store.upsert_leads([_lead(1, business_name="Thin Record")], merge=False)
    assert store.exportable(50)[0]["business_name"] == "Rich Record"


def test_rescrape_cannot_reset_send_status(store):
    """The expensive mistake: a re-scrape moving a messaged lead back to unsent."""
    store.upsert_leads([_lead(1)])
    store.mark_exported(["+12125550101"], "batch_001")
    store.upsert_leads([_lead(1)])
    assert store.exportable(50) == []
    assert store.counts(50)["exported"] == 1


def test_duplicates_within_one_batch_collapse(store):
    assert store.upsert_leads([_lead(1), _lead(1), _lead(2)])["inserted"] == 2


def test_below_threshold_is_stored_but_not_exportable(store):
    store.upsert_leads([_lead(1, score=30)])
    assert store.counts(50)["total"] == 1
    assert store.exportable(50) == []


# ------------------------------------------------------------------ export

def test_export_writes_csv_and_stamps(wired, store, tmp_path):
    store.upsert_leads([_lead(i) for i in range(1, 4)])
    report = Exporter(wired, store, log=lambda *a: None).run()

    assert report["written"] == 3
    path = tmp_path / "out" / "leads_batch_001.csv"
    rows = list(csv.DictReader(open(path, newline="")))
    assert len(rows) == 3
    assert rows[0]["Phone"].startswith("+1212555")
    assert rows[0]["service_type"] == "kitchen remodel"
    assert store.exportable(50) == []


def test_export_appends_to_the_suppression_list(wired, store, tmp_path):
    store.upsert_leads([_lead(1)])
    Exporter(wired, store, log=lambda *a: None).run()
    assert "+12125550101" in suppress.load(tmp_path / "suppression.txt")


def test_suppressed_phone_never_exports_even_at_max_score(wired, store, tmp_path):
    """The hard exclusion: score, status and requalification are all irrelevant."""
    suppress.merge(tmp_path / "suppression.txt", ["+12125550101"])
    store.upsert_leads([_lead(1, score=100), _lead(2, score=60)])
    report = Exporter(wired, store, log=lambda *a: None).run()
    assert report["written"] == 1
    assert report["stats"]["suppressed"] == 1
    rows = list(csv.DictReader(open(tmp_path / "out" / "leads_batch_001.csv", newline="")))
    assert rows[0]["Phone"] == "+12125550102"


def test_dry_run_stamps_nothing_and_suppresses_nothing(wired, store, tmp_path):
    store.upsert_leads([_lead(1)])
    Exporter(wired, store, log=lambda *a: None).run(dry_run=True)
    assert len(store.exportable(50)) == 1
    assert not (tmp_path / "suppression.txt").exists()


def test_lead_without_a_company_name_is_refused(wired, store):
    store.upsert_leads([_lead(1, business_name=None)])
    report = Exporter(wired, store, log=lambda *a: None).run()
    assert report["written"] == 0
    assert report["stats"]["no_company"] == 1


def test_batches_respect_batch_size_and_number_sequentially(wired, store, tmp_path):
    wired.data["export"]["batch_size"] = 2
    store.upsert_leads([_lead(i) for i in range(1, 6)])
    report = Exporter(wired, store, log=lambda *a: None).run()
    names = [f[0] for f in report["files"]]
    assert names == ["leads_batch_001.csv", "leads_batch_002.csv", "leads_batch_003.csv"]
    assert [f[1] for f in report["files"]] == [2, 2, 1]


def test_second_export_run_continues_the_numbering(wired, store, tmp_path):
    store.upsert_leads([_lead(1)])
    Exporter(wired, store, log=lambda *a: None).run()
    store.upsert_leads([_lead(2)])
    report = Exporter(wired, store, log=lambda *a: None).run()
    assert report["files"][0][0] == "leads_batch_002.csv"


def test_best_scores_export_first(wired, store):
    store.upsert_leads([_lead(1, score=55), _lead(2, score=95)])
    records, _, _ = Exporter(wired, store, log=lambda *a: None).prepare(
        store.exportable(50)
    )
    assert [r["phone"] for r in records] == ["+12125550102", "+12125550101"]


def test_qa_failure_blocks_the_lead_and_reports_why(wired, store):
    """A lead that would render a hole is refused, not silently blanked."""
    wired.data["messages"]["sequences"] = [
        {"name": "only", "requires": ["category_value"],
         "messages": [{"delay": "0m", "body": "saw your {category_value} work"}]}
    ]
    store.upsert_leads([_lead(1, category_value=None), _lead(2)])
    report = Exporter(wired, store, log=lambda *a: None).run()
    assert report["written"] == 1
    assert report["stats"]["no_sequence"] == 1
    assert report["rejects"][0]["phone"] == "+12125550101"


def test_webhook_verification_fails_closed_without_a_url(wired, store, monkeypatch):
    """Asking for verification and not getting it must not mean 'send anyway'."""
    monkeypatch.delenv("LEADPIPE_LINETYPE_URL", raising=False)
    wired.data["compliance"]["verification"]["mode"] = "webhook"
    store.upsert_leads([_lead(1)])
    report = Exporter(wired, store, log=lambda *a: None).run()
    assert report["written"] == 0
    assert report["stats"]["line_type"] == 1


def test_known_line_type_is_honoured(wired, store):
    wired.data["compliance"]["verification"]["mode"] = "webhook"
    store.upsert_leads([_lead(1, line_type="mobile"), _lead(2, line_type="landline")])
    report = Exporter(wired, store, log=lambda *a: None).run()
    assert report["written"] == 1
    assert report["stats"]["line_type"] == 1
