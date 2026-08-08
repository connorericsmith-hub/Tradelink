"""Categorizer behaviour. The refusals are the point."""
import pytest

from leadpipe.categorize import Categorizer


@pytest.fixture
def cat(cfg):
    return Categorizer(cfg)


def test_all_fixture_cases(cat, listings):
    for case in listings["categorize"]:
        got, reason = cat.derive(
            case["name"], case.get("primary", ""), case.get("categories", [])
        )
        assert (got or "") == case["expect"], (
            f"{case['name']!r} -> {got!r} (reason {reason}), "
            f"expected {case['expect']!r}: {case['why']}"
        )


def test_never_assigns_an_undeclared_value(cat, cfg, listings):
    declared = set(cfg.get("categorize.values"))
    for case in listings["categorize"]:
        got, _ = cat.derive(
            case["name"], case.get("primary", ""), case.get("categories", [])
        )
        assert got is None or got in declared


def test_veto_beats_the_name_it_would_have_matched(cat):
    """A pool company whose name also says 'bath' gets nothing, not 'bathroom
    remodel'. A wrong value is worse than none."""
    assert cat.derive("Aqua Pool and Bath Co", "Bathroom remodeler")[0] is None


def test_veto_fires_on_the_category_too(cat):
    """The name reads on-niche; the category says it is a landscaper."""
    assert cat.derive("Evergreen Kitchen Services", "Landscaper")[0] is None


def test_same_family_prefers_the_operators_own_word(cat):
    value, reason = cat.derive("Summit Countertops", "Kitchen remodeler")
    assert value == "countertop"
    assert reason == "name+primary_family"


def test_broad_name_yields_to_a_specific_category(cat):
    value, reason = cat.derive("Whole Home Remodel Group", "Bathroom remodeler")
    assert value == "bathroom remodel"
    assert reason == "primary_over_broad"


def test_cross_family_conflict_uses_the_configured_combination(cat):
    value, reason = cat.derive("Hearthstone Kitchen and Bath", "Contractor")
    assert value == "kitchen and bath remodel"
    assert reason.startswith("combined")


def test_cross_family_conflict_refuses_when_uncombinable(cat):
    assert cat.derive("Basement Tile Experts", "Contractor")[0] is None


def test_combine_refuse_mode_never_combines(cfg):
    cfg.data["categorize"]["combine"] = "refuse"
    cat = Categorizer(cfg)
    assert cat.derive("Hearthstone Kitchen and Bath", "Contractor")[0] is None


def test_ambiguous_category_assigns_nothing(cat):
    """'General contractor' covers several trades; a value from it is a guess."""
    assert cat.derive("Peralta Quality Services", "General contractor")[0] is None


def test_generic_primary_can_be_disabled_by_removing_the_mapping(cfg):
    cfg.data["categorize"]["generic_primary_patterns"] = {}
    cat = Categorizer(cfg)
    assert cat.derive("Fieldstone Company", "Remodeler")[0] is None


def test_values_outside_any_family_do_not_silently_corroborate(cfg):
    """Two unfamilied values must conflict, not be treated as related."""
    cfg.data["categorize"]["families"] = []
    cfg.data["categorize"]["combine"] = "refuse"
    cat = Categorizer(cfg)
    assert cat.derive("Summit Countertops", "Kitchen remodeler")[0] is None


def test_word_boundaries_stop_placename_false_positives(cat):
    """'Cabot Ridge' must not read as cabinetry."""
    value, _ = cat.derive("Cabot Ridge Builders", "Remodeler")
    assert value == "whole home remodel"


def test_multiple_name_signals_in_one_family_pick_most_specific(cat):
    value, reason = cat.derive("Northbrook Cabinet and Countertop", "Contractor")
    assert value == "countertop"
    assert reason == "name_multi_family"


def test_category_array_breaks_a_name_tie(cfg):
    cfg.data["categorize"]["combine"] = "refuse"
    cat = Categorizer(cfg)
    value, reason = cat.derive(
        "Summit Kitchen and Bath", "Contractor", ["Bathroom remodeler"]
    )
    assert value == "bathroom remodel"
    assert reason == "name_multi+category"


def test_no_signal_yields_nothing(cat):
    assert cat.derive("Vantage LLC", "Contractor")[0] is None
    assert cat.derive("", "")[0] is None
