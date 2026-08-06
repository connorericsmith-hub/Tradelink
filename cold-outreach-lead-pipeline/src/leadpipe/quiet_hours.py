"""Quiet hours in the LEAD's timezone.

The mistake this exists to prevent: you are in Chicago, you kick off a send at
8:05am because that is a polite hour where you are sitting, and it lands at
6:05am in Boston. In the US, unsolicited texts outside roughly 8am-9pm local
carry real legal exposure under the TCPA, and there is no general
business-to-business exemption — a small contractor's business line is very
often a personal mobile. Rules differ elsewhere; check your own jurisdiction.

This module answers "may I send to this lead right now" and "when may I". It
does not send anything, and it cannot stop your CRM from sending: if delivery
is scheduled by an automation, the send window has to be configured there too.
What this gives you is the ability to see, before you import, that 400 of your
leads are in a timezone your CRM's single global window handles wrongly.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.9+ has zoneinfo
    ZoneInfo = None


class QuietHours:
    def __init__(self, cfg) -> None:
        qh = cfg.get("compliance.quiet_hours") or {}
        self.earliest = _parse_time(qh.get("earliest", "08:00"))
        self.latest = _parse_time(qh.get("latest", "21:00"))
        self.source = qh.get("timezone_source", "lead_state")
        self.fixed = qh.get("fixed_timezone", "America/Chicago")
        self.state_timezones = {
            str(k).upper(): v for k, v in (qh.get("state_timezones") or {}).items()
        }

    def timezone_for(self, lead: dict) -> str | None:
        """Timezone name for a lead, or None when it cannot be determined.

        Unknown is returned rather than guessed. A lead whose timezone you do
        not know is one you cannot schedule safely, and defaulting it to your
        own is how the 6am text happens.
        """
        if self.source == "fixed":
            return self.fixed
        state = str(lead.get("state") or "").strip().upper()
        return self.state_timezones.get(state)

    def local_now(self, lead: dict, now: datetime | None = None) -> datetime | None:
        tzname = self.timezone_for(lead)
        if not tzname or ZoneInfo is None:
            return None
        try:
            tz = ZoneInfo(tzname)
        except Exception:
            return None
        base = now or datetime.now(tz)
        if base.tzinfo is None:
            base = base.replace(tzinfo=tz)
        return base.astimezone(tz)

    def is_open(self, lead: dict, now: datetime | None = None) -> tuple:
        """(allowed, reason). Unknown timezone is NOT allowed."""
        local = self.local_now(lead, now)
        if local is None:
            tzname = self.timezone_for(lead)
            if tzname and ZoneInfo is None:
                return False, "no timezone database available (install `tzdata`)"
            return False, "unknown timezone for state %r" % (lead.get("state") or "",)
        t = local.time()
        if t < self.earliest:
            return False, "%s local is before %s" % (
                local.strftime("%H:%M"), self.earliest.strftime("%H:%M"))
        if t >= self.latest:
            return False, "%s local is at or after %s" % (
                local.strftime("%H:%M"), self.latest.strftime("%H:%M"))
        return True, "%s local is inside the window" % local.strftime("%H:%M")

    def next_open(self, lead: dict, now: datetime | None = None) -> datetime | None:
        """The next moment this lead may be texted, in its own timezone."""
        local = self.local_now(lead, now)
        if local is None:
            return None
        today = local.replace(
            hour=self.earliest.hour, minute=self.earliest.minute,
            second=0, microsecond=0,
        )
        if local.time() < self.earliest:
            return today
        if local.time() < self.latest:
            return local
        return today + timedelta(days=1)

    def audit(self, leads) -> dict:
        """Timezone spread across a set of leads, and how many are unknown.

        Run this before an import. A large `unknown` count means your leads
        carry no usable state, and every quiet-hours guarantee downstream is
        resting on nothing.
        """
        by_tz = {}
        unknown = 0
        unknown_states = {}
        for lead in leads:
            tzname = self.timezone_for(lead)
            if not tzname:
                unknown += 1
                state = str(lead.get("state") or "") or "(blank)"
                unknown_states[state] = unknown_states.get(state, 0) + 1
                continue
            by_tz[tzname] = by_tz.get(tzname, 0) + 1
        return {
            "by_timezone": dict(sorted(by_tz.items(), key=lambda kv: -kv[1])),
            "unknown": unknown,
            "unknown_states": unknown_states,
            "distinct_timezones": len(by_tz),
        }


def _parse_time(value: str) -> time:
    text = str(value or "").strip()
    try:
        hh, mm = text.split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        raise ValueError("quiet-hours time %r is not HH:MM" % value)
