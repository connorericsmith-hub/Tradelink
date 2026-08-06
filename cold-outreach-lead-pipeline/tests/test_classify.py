"""Filter behaviour. The drops matter more than the keeps."""
import pytest

from leadpipe.classify import Classifier


@pytest.fixture
def clf(cfg):
    return Classifier(cfg)


def _generous(clf, name, primary="", categories=(), **kw):
    """Every favourable soft signal, so a drop proves structural rejection."""
    kw.setdefault("review_count", 40)
    kw.setdefault("rating", 4.8)
    kw.setdefault("website", "https://example.com")
    return clf.classify(name, primary, categories, **kw)


def test_fixture_drops_all_drop(clf, listings):
    for row in listings["drop"]:
        score, flags, verdict = _generous(
            clf, row["name"], row.get("primary", ""), row.get("categories", []),
            review_count=row.get("reviews", 40),
            rating=row.get("rating", 4.8),
            phone_type="toll_free" if str(row.get("phone", "")).startswith("+1800") else "other",
        )
        assert verdict == "drop", f"{row['name']!r} survived: {flags} ({row['why']})"


def test_fixture_keeps_all_pass(clf, listings):
    for row in listings["keep"]:
        score, flags, verdict = clf.classify(
            row["name"], row.get("primary", ""), row.get("categories", []),
            review_count=row.get("reviews"), rating=row.get("rating"),
            website=row.get("website"),
        )
        assert verdict == "pass", f"{row['name']!r} scored {score}: {flags}"


def test_fixture_low_never_passes(clf, listings):
    for row in listings["low"]:
        score, flags, verdict = clf.classify(
            row["name"], row.get("primary", ""), row.get("categories", []),
            review_count=row.get("reviews"), rating=row.get("rating"),
            website=row.get("website"),
        )
        assert verdict != "pass", f"{row['name']!r} reached {score}: {flags}"


def test_deny_scans_every_category_not_just_primary(clf):
    """The disqualifying label is often not the first one Google attached."""
    _, flags, verdict = _generous(
        clf, "Summit Home Renovations", "Remodeler",
        ["Remodeler", "Kitchen remodeler", "Roofing contractor"],
    )
    assert verdict == "drop"
    assert "roof" in flags[0]


def test_deny_scans_the_name(clf):
    _, flags, verdict = _generous(clf, "Ace Plumbing Co", "Kitchen remodeler", [])
    assert verdict == "drop"
    assert flags[0].endswith("@name")


def test_category_only_terms_do_not_fire_on_names(clf):
    """'Church Street Kitchens' is a prospect; a church is not."""
    _, _, verdict = clf.classify(
        "Church Street Kitchens", "Kitchen remodeler", ["Kitchen remodeler"],
        review_count=20, rating=4.7, website="https://example.com",
    )
    assert verdict == "pass"

    _, flags, venue = _generous(clf, "Grace Fellowship", "Church", ["Church"])
    assert venue == "drop" and "@category" in flags[0]


def test_whole_word_terms_do_not_false_drop(clf):
    """'pool' is whole-word, so Liverpool survives and a pool company does not."""
    _, _, ok = clf.classify(
        "Liverpool Kitchen and Bath", "Kitchen remodeler", ["Kitchen remodeler"],
        review_count=20, rating=4.7, website="https://example.com",
    )
    assert ok == "pass"

    _, flags, dropped = _generous(clf, "Blue Water Pool Builders", "Contractor", [])
    assert dropped == "drop" and "pool" in flags[0]


def test_prefix_terms_still_match_suffixed_trades(clf):
    """'roof' must catch 'roofing' and 'roofer' — trades suffix themselves."""
    for name in ("Apex Roofing", "Apex Roofer", "Apex Roof Repair"):
        _, _, verdict = _generous(clf, name, "Contractor", [])
        assert verdict == "drop", name


def test_review_ceiling_drops_franchises(clf):
    _, flags, verdict = _generous(
        clf, "Bath Fitter of Somewhere", "Bathroom remodeler", [], review_count=480
    )
    assert verdict == "drop" and "reviews_gt" in flags[0]


def test_toll_free_dropped(clf):
    _, flags, verdict = _generous(
        clf, "National Kitchen Pros", "Kitchen remodeler", [], phone_type="toll_free"
    )
    assert verdict == "drop" and flags == ["toll_free"]


def test_primary_deny_is_primary_only(clf):
    """An off-niche PRIMARY drops; the same label as a secondary does not."""
    _, _, dropped = _generous(clf, "Copper Leaf Design", "Interior designer", [])
    assert dropped == "drop"

    _, _, kept = clf.classify(
        "Copper Leaf Kitchens", "Kitchen remodeler",
        ["Kitchen remodeler", "Interior designer"],
        review_count=20, rating=4.7, website="https://example.com",
    )
    assert kept == "pass"


def test_bare_category_cannot_export(clf):
    """The invariant: a category claim alone is never enough."""
    score, _, verdict = clf.classify("Vantage LLC", "Kitchen remodeler", [])
    assert verdict != "pass"
    assert score < clf.threshold


def test_core_primary_and_secondary_never_stack(clf):
    both = clf.classify(
        "Vantage LLC", "Kitchen remodeler", ["Kitchen remodeler", "Cabinet maker"],
        review_count=20, rating=4.7, website="https://example.com",
    )[0]
    one = clf.classify(
        "Vantage LLC", "Kitchen remodeler", ["Kitchen remodeler"],
        review_count=20, rating=4.7, website="https://example.com",
    )[0]
    assert both == one


def test_name_signal_cap_holds(clf):
    """A keyword-stuffed name cannot buy its way past the cap."""
    stuffed = clf.classify("Kitchen Bath Remodel Renovation Cabinet Countertop Co")[0]
    cap = clf.w["name_signal_cap"] + clf.w["no_website_no_reviews"]
    assert stuffed == cap


def test_zero_reviews_is_treated_as_no_data(clf):
    """A scraper's 0 means 'unreviewed', and must not earn the review bonus."""
    score, flags, _ = clf.classify(
        "Hearthstone Kitchens", "Kitchen remodeler", [],
        review_count=None, rating=None, website="https://example.com",
    )
    assert "reviews_in_band" not in flags


def test_empty_primary_still_scans_secondaries(clf):
    score, flags, _ = clf.classify(
        "Vantage LLC", "", ["Cabinet maker"],
        review_count=20, rating=4.7, website="https://example.com",
    )
    assert any(f.startswith("core_secondary") for f in flags)
