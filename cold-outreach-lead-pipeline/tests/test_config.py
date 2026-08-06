"""The validator is what a stranger relies on. It has to actually catch things."""
import pytest

from leadpipe import config as config_mod
from leadpipe.selftest import run as run_selftest


def _problems(cfg):
    return " | ".join(config_mod.validate(cfg))


def test_shipped_example_is_valid(cfg):
    assert config_mod.validate(cfg) == []


def test_selftest_passes_on_the_shipped_example(cfg):
    assert run_selftest(cfg, log=lambda *a: None) == 0


def test_missing_required_key_is_caught(cfg):
    del cfg.data["score"]["export_threshold"]
    assert "score.export_threshold" in _problems(cfg)


def test_broken_regex_is_caught(cfg):
    cfg.data["categorize"]["veto_patterns"].append(r"\b(unclosed")
    assert "does not compile" in _problems(cfg)


def test_undeclared_category_value_is_caught(cfg):
    cfg.data["categorize"]["primary_patterns"]["kitchen remodeler"] = "not a value"
    assert "not in categorize.values" in _problems(cfg)


def test_unreachable_category_value_is_caught(cfg):
    cfg.data["categorize"]["values"].append("orphan value")
    assert "no pattern can ever assign it" in _problems(cfg)


def test_scoring_invariant_violation_is_caught(cfg):
    """The most dangerous config mistake: making a category claim sufficient."""
    cfg.data["score"]["weights"]["core_primary_category"] = 90
    problems = _problems(cfg)
    assert "SCORING INVARIANT BROKEN" in problems
    assert "deny list" in problems


def test_weak_only_ceiling_violation_is_caught(cfg):
    cfg.data["score"]["weights"]["weak_name_signal"] = 40
    assert "weak-name-only" in _problems(cfg).lower() or "WEAK" in _problems(cfg)


def test_undeclared_merge_field_is_caught(cfg):
    cfg.data["messages"]["sequences"][1]["messages"][0]["body"] = "hi {nickname}"
    assert "not in messages.merge_fields" in _problems(cfg)


def test_merging_an_optional_field_without_requiring_it_is_caught(cfg):
    cfg.data["messages"]["sequences"][1]["messages"][0]["body"] = "hi {category_value}"
    assert "would be sent a message with a blank in it" in _problems(cfg)


def test_guaranteed_fields_may_be_merged_without_being_required(cfg):
    """`company` is guaranteed by export.require_company_name."""
    cfg.data["messages"]["sequences"][1]["requires"] = []
    cfg.data["messages"]["sequences"][1]["messages"][0]["body"] = "hi {company}"
    assert config_mod.validate(cfg) == []


def test_company_stops_being_guaranteed_when_not_required(cfg):
    cfg.data["export"]["require_company_name"] = False
    cfg.data["messages"]["sequences"][1]["requires"] = []
    assert "blank in it" in _problems(cfg)


def test_missing_catch_all_sequence_is_caught(cfg):
    cfg.data["messages"]["sequences"][1]["requires"] = ["category_value"]
    assert "no catch-all sequence" in _problems(cfg)


def test_bad_quiet_hours_format_is_caught(cfg):
    cfg.data["compliance"]["quiet_hours"]["earliest"] = "8am"
    assert "expected HH:MM" in _problems(cfg)


def test_lead_state_mode_without_a_map_is_caught(cfg):
    cfg.data["compliance"]["quiet_hours"]["state_timezones"] = {}
    assert "state_timezones is empty" in _problems(cfg)


def test_bad_location_is_caught(cfg):
    cfg.data["scrape"]["locations"][0]["state"] = "Texas"
    assert "expected a 2-letter code" in _problems(cfg)


def test_combined_values_unreachable_in_refuse_mode_is_caught(cfg):
    cfg.data["categorize"]["combine"] = "refuse"
    assert "can never be assigned" in _problems(cfg)


def test_selftest_fails_when_the_config_is_broken(cfg):
    cfg.data["score"]["weights"]["core_primary_category"] = 90
    assert run_selftest(cfg, log=lambda *a: None) == 1
