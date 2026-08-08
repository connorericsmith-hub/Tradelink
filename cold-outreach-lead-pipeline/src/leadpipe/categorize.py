"""Assign each lead the one category value its message merges in.

Corroboration-first, and it refuses when unsure. Naming a trade the operator
does not do is worse than naming none: a wrong value proves you are a bot on
the first line, to a business owner who was already suspicious. A lead with no
value is not a failure — it routes to a sequence that doesn't need one.

Signals, in order of authority:
  1. business name    strongest; the operator chose it
  2. primary category Google's canonical label
  3. category array   everything else attached to the listing

Resolution, in order:
  veto matches anywhere                 -> nothing
  one name value, category agrees       -> that value
  one name value, category silent       -> that value
  one name value, category disagrees    -> same family: the name wins
                                           broad name: the category wins
                                           otherwise: a conflict (see below)
  several name values, category picks   -> the backed one
  several name values, one family       -> the most specific
  several name values, several families -> a conflict (see below)
  name silent, category speaks          -> the category value
  nothing                               -> nothing

A conflict is resolved by `categorize.combine`: `refuse` assigns nothing,
`combined` looks the pair up in `combined_values` and refuses if it is absent.
"""
from __future__ import annotations

import re

from .config import Config


class Categorizer:
    def __init__(self, cfg: Config) -> None:
        c = cfg.get("categorize") or {}
        self.field_name = c.get("field_name") or "category_value"
        self.values = list(c.get("values") or [])

        self.name_patterns = []
        for entry in c.get("name_patterns") or []:
            if isinstance(entry, dict) and entry.get("pattern") and entry.get("value"):
                self.name_patterns.append(
                    (re.compile(entry["pattern"], re.I), entry["value"])
                )

        self.veto = [re.compile(p, re.I) for p in (c.get("veto_patterns") or [])]

        self.primary = {k.lower(): v for k, v in (c.get("primary_patterns") or {}).items()}
        self.generic_primary = {
            k.lower(): v for k, v in (c.get("generic_primary_patterns") or {}).items()
        }
        self.category_map = {
            k.lower(): v for k, v in (c.get("category_patterns") or {}).items()
        }

        self.families = [frozenset(f) for f in (c.get("families") or [])]
        self.broad_value = c.get("broad_value")
        self.specificity = list(c.get("specificity") or [])
        self.combine = c.get("combine", "refuse")

        self.combined = {}
        for entry in c.get("combined_values") or []:
            when = frozenset((entry or {}).get("when") or [])
            value = (entry or {}).get("value")
            if when and value:
                self.combined[when] = value

    # ---------------------------------------------------------------- API

    def derive(self, name: str, primary_category: str = "", categories=()) -> tuple:
        """Return (value_or_None, reason). The reason is for run reports and
        for working out why a lead you expected to be personalized wasn't."""
        if self.vetoed(name, primary_category):
            return None, "veto:off_trade_business"

        names = self._name_values(name)
        prim = self._primary_value(primary_category, allow_generic=True)
        prim_specific = self._primary_value(primary_category, allow_generic=False)
        cats = self._category_values(categories)

        if len(names) == 1:
            return self._one_name(names[0], prim_specific, cats)
        if len(names) > 1:
            return self._many_names(names, prim_specific, cats)

        # The name says nothing specific: fall back to the category signal.
        if prim:
            return prim, ("primary_generic" if prim_specific is None else "primary")
        if len(cats) == 1:
            return cats[0], "category"
        if len(cats) > 1:
            return self._conflict(cats, "category_multi")
        return None, "no_signal"

    def value_for(self, name, primary_category="", categories=()) -> str:
        """Just the value, empty string when there isn't one."""
        value, _ = self.derive(name, primary_category, categories)
        return value or ""

    def vetoed(self, name: str, primary_category: str = "") -> bool:
        """True when the business is plainly something else.

        Checked on the name AND the category, because each catches what the
        other misses: the name catches a marine contractor that Google labels
        a deck builder, the category catches a tree service whose name alone
        reads on-niche.
        """
        hay = "%s %s" % (name or "", primary_category or "")
        return any(rx.search(hay) for rx in self.veto)

    # ----------------------------------------------------------- internals

    def _one_name(self, n, prim_specific, cats):
        if prim_specific and prim_specific != n and n not in cats:
            if self.broad_value and n == self.broad_value:
                # The name is true but says almost nothing ("Home Remodeling
                # LLC") while the category names a real trade. Two true
                # statements; the specific one is the useful one.
                return prim_specific, "primary_over_broad"
            if self._same_family(n, prim_specific):
                # The same work at two grains. The operator's own word wins.
                return n, "name+primary_family"
            return self._conflict([n, prim_specific], "name_vs_primary")
        return n, ("name+primary" if prim_specific == n else "name")

    def _many_names(self, names, prim_specific, cats):
        if prim_specific and prim_specific in names:
            return prim_specific, "name_multi+primary"
        backed = [n for n in names if n in cats]
        if len(backed) == 1:
            return backed[0], "name_multi+category"
        fams = [self._family(n) for n in names]
        if fams[0] is not None and all(f == fams[0] for f in fams):
            best = names[0]
            for n in names[1:]:
                best = self._more_specific(best, n)
            return best, "name_multi_family"
        return self._conflict(names, "name_multi")

    def _conflict(self, candidates, kind):
        """Cross-family disagreement. `combined` may name the pair; otherwise
        refuse — an unresolved conflict is exactly the case where guessing
        costs more than staying generic."""
        if self.combine == "combined":
            value = self.combined.get(frozenset(candidates))
            if value:
                return value, "combined:%s" % kind
        return None, "%s:%s" % (
            "ambiguous" if len(candidates) > 2 else "conflict",
            ",".join(sorted(str(c) for c in candidates)),
        )

    def _name_values(self, name):
        """Distinct values visible in the name, in config order (specific
        first). Config order is load-bearing: the first pattern that matches
        within a family is the one that wins."""
        nm = (name or "").lower()
        out = []
        for rx, value in self.name_patterns:
            if rx.search(nm) and value not in out:
                out.append(value)
        return out

    def _primary_value(self, primary_category, allow_generic):
        p = (primary_category or "").strip().lower()
        if p in self.primary:
            return self.primary[p]
        if allow_generic and p in self.generic_primary:
            return self.generic_primary[p]
        return None

    def _category_values(self, categories):
        out = []
        for c in categories or ():
            value = self.category_map.get(str(c or "").strip().lower())
            if value and value not in out:
                out.append(value)
        return out

    def _family(self, value):
        for f in self.families:
            if value in f:
                return f
        return None

    def _same_family(self, a, b):
        """Two values are same-family only if a family actually contains them.

        Values in NO family are not silently treated as related — that would
        make every unfamilied pair corroborate each other, which is the
        opposite of what an unmapped value should do.
        """
        fa, fb = self._family(a), self._family(b)
        return fa is not None and fa == fb

    def _more_specific(self, a, b):
        order = {v: i for i, v in enumerate(self.specificity)}
        big = len(order) + 1
        return a if order.get(a, big) <= order.get(b, big) else b


def coverage(rows, categorizer) -> dict:
    """How many leads got a value, and which values. The single most useful
    number when tuning section 4 of the config: low coverage means your
    patterns are too narrow, and suspiciously high coverage means your veto
    list is too thin."""
    counts = {}
    reasons = {}
    for r in rows:
        value, reason = categorizer.derive(
            r.get("business_name") or "",
            r.get("primary_category") or "",
            r.get("categories") or (),
        )
        counts[value or ""] = counts.get(value or "", 0) + 1
        key = reason.split(":")[0]
        reasons[key] = reasons.get(key, 0) + 1
    total = sum(counts.values())
    assigned = total - counts.get("", 0)
    return {
        "total": total,
        "assigned": assigned,
        "pct": (assigned / total) if total else 0.0,
        "by_value": counts,
        "by_reason": reasons,
    }
