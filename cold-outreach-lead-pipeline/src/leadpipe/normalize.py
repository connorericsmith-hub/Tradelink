"""Phone and address normalization.

Every phone becomes E.164 (+1XXXXXXXXXX) or it is discarded. This is the
dedupe key for the whole pipeline: "(214) 555-0100", "214-555-0100" and
"+1 214 555 0100" are one lead, and without a canonical form you will text
them three times.
"""
from __future__ import annotations

import json
import re

try:
    import phonenumbers
except ImportError:  # pragma: no cover - dependency is declared, this is a guard
    phonenumbers = None

_STATE_RE = re.compile(r",\s*([A-Za-z]{2})\s+\d{5}")
_CITY_RE = re.compile(r",\s*([^,]+),\s*[A-Za-z]{2}\s+\d{5}")


def normalize_phone(raw: str, region: str = "US") -> str | None:
    """E.164, or None when the number is unparseable or not a valid number.

    Validity here is structural (the number could exist in the plan), not a
    claim that it is in service or can receive SMS. See compliance.verification.
    """
    if not raw:
        return None
    if phonenumbers is None:
        raise RuntimeError(
            "the 'phonenumbers' package is required. Install the project with "
            "`pip install -e .`"
        )
    try:
        num = phonenumbers.parse(str(raw), region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(num):
        return None
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


def state_from_address(addr: str) -> str:
    m = _STATE_RE.search(addr or "")
    return m.group(1).upper() if m else ""


def city_from_address(addr: str) -> str:
    m = _CITY_RE.search(addr or "")
    return m.group(1).strip() if m else ""


def _num(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def load_scraper_records(path) -> list:
    """Load scraper output: a JSON array or newline-delimited JSON.

    Tolerant of both because scrapers disagree, and a run that dies on a
    format difference after an hour of scraping is an expensive way to find
    out which one you got.
    """
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read().strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def parse_listing(row: dict, metro: str = "", state: str = "") -> dict | None:
    """Map one raw scraper record to the pipeline's lead shape.

    Returns None when there is no usable phone — a lead you cannot contact is
    not a lead. Field names cover the common variants across scrapers
    (`title`/`name`, `web_site`/`website`, `review_rating`/`rating`).
    """
    phone_raw = str(row.get("phone") or "").strip()
    e164 = normalize_phone(phone_raw)
    if not e164:
        return None

    name = str(row.get("title") or row.get("name") or "").strip()
    cats = [str(c).strip() for c in (row.get("categories") or []) if str(c).strip()]
    primary = str(row.get("category") or row.get("primary_type") or "").strip()
    if not primary and cats:
        primary = cats[0]

    # Scrapers emit 0 rather than null for an unreviewed business; 0 means
    # "no data", and scoring a 0 as a real review count penalises exactly the
    # young operators worth reaching.
    review_count = _num(row.get("review_count") or row.get("reviews"))
    review_count = int(review_count) if review_count else None
    rating = _num(row.get("review_rating") or row.get("rating")) or None
    website = str(row.get("web_site") or row.get("website") or "").strip()

    addr = str(row.get("address") or "").strip()
    complete = row.get("complete_address")
    complete = complete if isinstance(complete, dict) else {}
    # Some scrapers put a JSON blob in `address`; prefer a clean string.
    clean_addr = addr if addr and not addr.startswith("{") else ""

    comp_state = str(complete.get("state") or "").strip()
    rec_state = (
        state_from_address(clean_addr)
        or (comp_state if len(comp_state) == 2 else "")
        or state
        or ""
    )
    city = str(complete.get("city") or "").strip() or city_from_address(clean_addr)

    return {
        "business_name": name or None,
        "phone_e164": e164,
        "phone_raw": phone_raw or None,
        "primary_category": primary or None,
        "categories": cats,
        "rating": rating,
        "review_count": review_count,
        "website": website or None,
        "address": clean_addr or None,
        "city": city or None,
        "state": (rec_state or "").upper()[:2] or None,
        "metro": metro or None,
    }
