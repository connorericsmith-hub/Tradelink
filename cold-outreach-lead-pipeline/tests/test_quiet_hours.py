"""Quiet hours resolve in the LEAD's timezone, not the operator's."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from leadpipe.quiet_hours import QuietHours


@pytest.fixture
def qh(cfg):
    return QuietHours(cfg)


def _at(tz, hour, minute=0):
    return datetime(2026, 6, 15, hour, minute, tzinfo=ZoneInfo(tz))


def test_state_maps_to_a_timezone(qh):
    assert qh.timezone_for({"state": "TX"}) == "America/Chicago"
    assert qh.timezone_for({"state": "ca"}) == "America/Los_Angeles"


def test_unknown_state_is_unknown_not_guessed(qh):
    """A lead you cannot place is one you cannot schedule. Never default it."""
    assert qh.timezone_for({"state": ""}) is None
    assert qh.timezone_for({"state": "ZZ"}) is None
    allowed, why = qh.is_open({"state": ""}, _at("America/Chicago", 13))
    assert not allowed
    assert "unknown timezone" in why


def test_the_6am_boston_case(qh):
    """The mistake this module exists to prevent: 8:05am Central is 9:05am
    Eastern (fine) but 6:05am Pacific (not)."""
    send_moment = _at("America/Chicago", 8, 5)
    assert qh.is_open({"state": "TX"}, send_moment)[0]
    assert qh.is_open({"state": "NY"}, send_moment)[0]
    assert not qh.is_open({"state": "CA"}, send_moment)[0]


def test_window_edges(qh):
    assert not qh.is_open({"state": "TX"}, _at("America/Chicago", 7, 59))[0]
    assert qh.is_open({"state": "TX"}, _at("America/Chicago", 8, 0))[0]
    assert qh.is_open({"state": "TX"}, _at("America/Chicago", 20, 59))[0]
    assert not qh.is_open({"state": "TX"}, _at("America/Chicago", 21, 0))[0]


def test_next_open_is_today_when_too_early(qh):
    nxt = qh.next_open({"state": "TX"}, _at("America/Chicago", 6, 0))
    assert nxt.hour == 8 and nxt.day == 15


def test_next_open_rolls_to_tomorrow_when_too_late(qh):
    nxt = qh.next_open({"state": "TX"}, _at("America/Chicago", 22, 0))
    assert nxt.hour == 8 and nxt.day == 16


def test_next_open_is_now_when_inside_the_window(qh):
    now = _at("America/Chicago", 13, 30)
    assert qh.next_open({"state": "TX"}, now) == now


def test_fixed_mode_ignores_the_lead_state(cfg):
    cfg.data["compliance"]["quiet_hours"]["timezone_source"] = "fixed"
    qh = QuietHours(cfg)
    assert qh.timezone_for({"state": "CA"}) == "America/Chicago"


def test_audit_counts_unknowns(qh):
    leads = [{"state": "TX"}, {"state": "TX"}, {"state": "CA"}, {"state": ""}]
    report = qh.audit(leads)
    assert report["by_timezone"]["America/Chicago"] == 2
    assert report["unknown"] == 1
    assert report["unknown_states"]["(blank)"] == 1


def test_arizona_does_not_observe_dst(qh):
    """A real trap: Phoenix is Mountain in winter and Pacific in summer."""
    summer = datetime(2026, 7, 1, 20, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    # 20:30 Pacific in July is 20:30 in Phoenix (both UTC-7), still inside.
    assert qh.is_open({"state": "AZ"}, summer)[0]
    winter = datetime(2026, 1, 15, 20, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    # 20:30 Pacific in January is 21:30 in Phoenix (UTC-7 vs UTC-8): blocked.
    assert not qh.is_open({"state": "AZ"}, winter)[0]
