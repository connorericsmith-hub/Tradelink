"""Deny-first scored classifier.

Two stages, in this order and only this order:

  1. DENY. Every deny rule is checked against the business name and against
     EVERY category on the listing. One hit is fatal and no score is computed.
  2. SCORE. Survivors accumulate points; `export_threshold` gates the export.

Scoring first and filtering second is the mistake this design exists to
prevent. A plumbing company with 60 five-star reviews and a good website
out-scores a real prospect with none of those things, and if score alone
decides, the plumber ships. Deny first: no amount of quality makes a business
in the wrong trade worth a message.

The second load-bearing rule is that a bare category match cannot export.
Google's category is a claim, not evidence — many of the businesses you just
denied also carry it. At least one independent signal has to corroborate.
`config.score_report()` proves this holds for the configured weights, and
`leadpipe selftest` fails if it doesn't.
"""
from __future__ import annotations

from .config import Config
from .textmatch import compile_terms, contains_any, first_hit


class Classifier:
    """Compiled, reusable classifier for one config."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        f = cfg.get("filter") or {}
        s = cfg.get("score") or {}

        self.exclude_terms = [t.lower() for t in (f.get("exclude_terms") or [])]
        self.deny_terms = [t.lower() for t in (f.get("deny_terms") or [])]
        category_only = {t.lower() for t in (f.get("category_only_terms") or [])}
        whole_word = {t.lower() for t in (f.get("whole_word_terms") or [])}

        # One compiled table for both lists; the name/category split is a
        # difference in which TERMS are checked, not in how they match.
        self._re = compile_terms(self.exclude_terms + self.deny_terms, whole_word)

        # Venue/institution words are checked on categories only, because
        # business names steal them ("Church Street Kitchens" is a prospect).
        self.name_deny_terms = [t for t in self.deny_terms if t not in category_only]
        self.name_exclude_terms = [t for t in self.exclude_terms if t not in category_only]

        self.primary_deny = [t.lower() for t in (f.get("primary_category_deny") or [])]
        self.max_reviews = f.get("max_reviews")
        self.drop_toll_free = bool(f.get("drop_toll_free", True))
        self.require_no_website = bool(f.get("require_no_website", False))

        self.core_categories = [c.lower() for c in (s.get("core_categories") or [])]
        self.name_signals = [c.lower() for c in (s.get("name_signals") or [])]
        self.weak_signals = [c.lower() for c in (s.get("weak_name_signals") or [])]
        self.threshold = int(s.get("export_threshold", 50))
        self.w = dict(s.get("weights") or {})
        band = s.get("review_band") or [1, 80]
        self.review_low, self.review_high = int(band[0]), int(band[1])
        self.rating_threshold = float(s.get("rating_threshold", 4.3))

    # ----------------------------------------------------------------- API

    def classify(
        self,
        name: str,
        primary_category: str = "",
        categories=(),
        review_count: int | None = None,
        rating: float | None = None,
        website: str | None = None,
        phone_type: str | None = None,
        verified_directory: bool = False,
    ) -> tuple[int, list, str]:
        """Return (score, flags, verdict) with verdict in drop|pass|low.

        `pass` means score >= export_threshold. `low` means the listing is
        plausible but uncorroborated: it is stored (a later enrichment pass
        may lift it) but never exported.
        """
        nm = (name or "").lower()
        primary = (primary_category or "").lower()

        cats = [primary] if primary else []
        for c in categories or ():
            c = str(c or "").lower().strip()
            if c and c not in cats:
                cats.append(c)

        drop = self._deny(nm, primary, cats, review_count, phone_type, website)
        if drop is not None:
            return 0, [drop], "drop"

        return self._score(nm, primary, cats, review_count, rating, website,
                           verified_directory)

    # ------------------------------------------------------------- stage 1

    def _deny(self, nm, primary, cats, review_count, phone_type, website=None):
        """First fatal reason, or None. Order is deliberate: cheap and most
        common reasons first, so run reports show the real shape of the noise."""
        # Checked first and offline: a business that already has a website is
        # not a prospect when the offer IS building one, regardless of trade,
        # score, or anything else about the listing.
        if self.require_no_website and (website or "").strip():
            return "has_website"

        # The name is checked against the non-venue terms; every category is
        # checked against all of them. Google attaches up to ten categories
        # and a disqualifying one can sit anywhere in that array, so all of
        # them are checked — not just the primary.
        targets = [("name", nm, self.name_exclude_terms, self.name_deny_terms)]
        targets += [("category", c, self.exclude_terms, self.deny_terms) for c in cats]

        for label, text, excl, deny in targets:
            hit = first_hit(text, self._re, excl)
            if hit:
                return "exclude:%s@%s" % (hit, label)
            hit = first_hit(text, self._re, deny)
            if hit:
                return "deny:%s@%s" % (hit, label)

        off = contains_any(primary, self.primary_deny)
        if off:
            return "primary_off_niche:%s" % off

        if self.max_reviews is not None and review_count is not None:
            if review_count > int(self.max_reviews):
                return "reviews_gt_%s" % self.max_reviews

        if self.drop_toll_free and phone_type == "toll_free":
            return "toll_free"
        return None

    # ------------------------------------------------------------- stage 2

    def _score(self, nm, primary, cats, review_count, rating, website, verified):
        def w(key, default=0):
            return int(self.w.get(key, default))

        score = 0
        flags = []

        # A core category scores once: primary or secondary, never both.
        core_primary = contains_any(primary, self.core_categories)
        core_secondary = None
        if core_primary:
            score += w("core_primary_category")
            flags.append("core_primary:%s" % core_primary)
        else:
            # When primary is empty, cats holds only secondaries.
            for c in (cats[1:] if primary else cats):
                core_secondary = contains_any(c, self.core_categories)
                if core_secondary:
                    score += w("core_secondary_category")
                    flags.append("core_secondary:%s" % core_secondary)
                    break

        # Strong name signals, capped. The cap stops a keyword-stuffed name
        # ("Kitchen Bath Remodel Renovation Cabinet Co") from buying an export.
        sigs = []
        for s in self.name_signals:
            if s in nm and s not in sigs:
                sigs.append(s)
        if sigs:
            score += min(w("name_signal_each") * len(sigs), w("name_signal_cap"))
            flags.extend("name:%s" % s for s in sigs)

        weak = [s for s in self.weak_signals if s in nm]
        if weak:
            score += w("weak_name_signal")
            flags.append("weak:" + ",".join(weak))
            if not core_primary and not core_secondary and not sigs:
                flags.append("weak_only")

        if review_count is not None and self.review_low <= review_count <= self.review_high:
            score += w("reviews_in_band")
            flags.append("reviews_in_band")

        if rating is not None and float(rating) >= self.rating_threshold:
            score += w("rating_at_or_above")
            flags.append("rating_ok")

        if (website or "").strip():
            score += w("has_website")
            flags.append("website")
        elif not review_count and rating is None:
            # No site, no reviews, no rating: a stub listing or a dead
            # business. Not fatal — some real one-truck operators look like
            # this — but it must not clear the bar on a category alone.
            score += w("no_website_no_reviews")
            flags.append("no_web_no_reviews")

        if verified:
            score += w("verified_directory")
            flags.append("verified_directory")

        return score, flags, ("pass" if score >= self.threshold else "low")

    def is_kept(self, verdict: str) -> bool:
        """Worth storing. The exporter gates separately on the threshold, so
        `low` rows are kept: enrichment can lift them later without a rescrape."""
        return verdict != "drop"


def phone_type_of(e164: str) -> str:
    """toll_free | other | unknown, offline, from the number's own metadata.

    This is the ONLY line-type signal available without paying someone. It
    catches toll-free reliably and says nothing about landline vs mobile,
    because in the North American plan that distinction is not encoded in the
    number — see compliance.verification in the config.
    """
    try:
        import phonenumbers
    except ImportError:
        return "unknown"
    try:
        num = phonenumbers.parse(e164, "US")
        if phonenumbers.number_type(num) == phonenumbers.PhoneNumberType.TOLL_FREE:
            return "toll_free"
        return "other"
    except Exception:
        return "unknown"
