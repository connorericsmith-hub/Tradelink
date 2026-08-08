"""Config loading and validation.

One YAML file drives the whole pipeline. Nothing niche-specific lives in code,
so this module is the only place that knows the config's shape — and the
`validate()` below is what a stranger runs to find out their config is wrong
before they spend a scrape finding out the expensive way.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_NAMES = ("config.yaml", "config.yml")


class ConfigError(Exception):
    """Raised when the config is unusable. Message is aimed at the operator."""


class Config:
    """Dict-backed config with dotted lookup.

    Deliberately thin: the value of this file is `validate()`, not an object
    model that has to be kept in sync with the YAML by hand.
    """

    def __init__(self, data: dict, path: Path | None = None) -> None:
        self.data = data
        self.path = path

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, dotted: str) -> Any:
        val = self.get(dotted, _MISSING)
        if val is _MISSING:
            raise ConfigError(f"config is missing required key: {dotted}")
        return val

    @property
    def root(self) -> Path:
        """Directory the config lives in. Relative paths resolve against it."""
        return self.path.parent if self.path else Path.cwd()

    def path_for(self, dotted: str, default: str) -> Path:
        raw = str(self.get(dotted, default))
        p = Path(raw).expanduser()
        return p if p.is_absolute() else (self.root / p)


_MISSING = object()


def find_config(explicit: str | None = None) -> Path:
    """Locate the config file: explicit path, $LEADPIPE_CONFIG, then cwd."""
    if explicit:
        p = Path(explicit).expanduser()
        if not p.exists():
            raise ConfigError(f"no config file at {p}")
        return p
    env = os.environ.get("LEADPIPE_CONFIG")
    if env:
        p = Path(env).expanduser()
        if not p.exists():
            raise ConfigError(f"LEADPIPE_CONFIG points at {p}, which does not exist")
        return p
    for name in DEFAULT_CONFIG_NAMES:
        p = Path.cwd() / name
        if p.exists():
            return p
    raise ConfigError(
        "no config file found. Copy config.example.yaml to config.yaml and edit it, "
        "or pass --config /path/to/config.yaml"
    )


def load(explicit: str | None = None) -> Config:
    path = find_config(explicit)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a YAML mapping at the top level")
    return Config(data, path)


# --------------------------------------------------------------------- checks

def validate(cfg: Config) -> list[str]:
    """Return a list of problems. Empty list means the config is coherent.

    Checks structure, then cross-references: every value referenced by the
    categorizer must be declared, every merge field used by a message must be
    available, and the score weights must make a bare category match
    non-exportable. That last one is the invariant most worth protecting and
    the easiest to break by nudging a weight.
    """
    problems: list[str] = []
    problems += _check_required(cfg)
    problems += _check_scrape(cfg)
    problems += _check_regexes(cfg)
    problems += _check_categorize(cfg)
    problems += _check_messages(cfg)
    problems += _check_scoring_invariant(cfg)
    problems += _check_compliance(cfg)
    return problems


_REQUIRED = (
    "niche.name",
    "scrape.keywords",
    "scrape.locations",
    "filter.deny_terms",
    "score.export_threshold",
    "score.weights",
    "score.core_categories",
    "categorize.field_name",
    "categorize.values",
    "export.columns",
    "messages.sequences",
)


def _check_required(cfg: Config) -> list[str]:
    out = []
    for key in _REQUIRED:
        if cfg.get(key, _MISSING) is _MISSING:
            out.append(f"missing required key: {key}")
    return out


def _check_scrape(cfg: Config) -> list[str]:
    out = []
    keywords = cfg.get("scrape.keywords") or []
    if not keywords:
        out.append("scrape.keywords is empty — there is nothing to search for")
    locations = cfg.get("scrape.locations") or []
    if not locations:
        out.append("scrape.locations is empty — there is nowhere to search")
    for i, loc in enumerate(locations):
        if not isinstance(loc, dict):
            out.append(f"scrape.locations[{i}] must be a mapping")
            continue
        for field in ("metro", "state", "anchor"):
            if not str(loc.get(field) or "").strip():
                out.append(f"scrape.locations[{i}] is missing '{field}'")
        state = str(loc.get("state") or "")
        if state and len(state) != 2:
            out.append(
                f"scrape.locations[{i}].state is {state!r}; expected a 2-letter code"
            )
    depth = cfg.get("scrape.depth", 10)
    if not isinstance(depth, int) or depth < 1:
        out.append("scrape.depth must be a positive integer")
    return out


def _check_regexes(cfg: Config) -> list[str]:
    """Every user-supplied regex must compile. A bad one is a silent no-match."""
    out = []
    for entry in cfg.get("categorize.name_patterns") or []:
        pat = (entry or {}).get("pattern") if isinstance(entry, dict) else None
        if pat is None:
            out.append(f"categorize.name_patterns entry has no 'pattern': {entry!r}")
            continue
        try:
            re.compile(pat)
        except re.error as e:
            out.append(f"categorize.name_patterns pattern {pat!r} does not compile: {e}")
    for pat in cfg.get("categorize.veto_patterns") or []:
        try:
            re.compile(pat)
        except re.error as e:
            out.append(f"categorize.veto_patterns pattern {pat!r} does not compile: {e}")
    return out


def _check_categorize(cfg: Config) -> list[str]:
    """Nothing may reference a value that is not declared in `values`."""
    out = []
    values = set(cfg.get("categorize.values") or [])
    if not values:
        return ["categorize.values is empty — no lead can ever be categorized"]

    def _ref(where: str, value: str) -> None:
        if value not in values:
            out.append(
                f"{where} assigns {value!r}, which is not in categorize.values"
            )

    for entry in cfg.get("categorize.name_patterns") or []:
        if isinstance(entry, dict) and "value" in entry:
            _ref(f"categorize.name_patterns[{entry.get('pattern')!r}]", entry["value"])
    for key in ("primary_patterns", "generic_primary_patterns", "category_patterns"):
        for cat, value in (cfg.get(f"categorize.{key}") or {}).items():
            _ref(f"categorize.{key}[{cat!r}]", value)
    for fam in cfg.get("categorize.families") or []:
        for value in fam:
            _ref("categorize.families", value)
    for value in cfg.get("categorize.specificity") or []:
        _ref("categorize.specificity", value)

    broad = cfg.get("categorize.broad_value")
    if broad:
        _ref("categorize.broad_value", broad)

    combine = cfg.get("categorize.combine", "refuse")
    if combine not in ("refuse", "combined"):
        out.append(
            f"categorize.combine is {combine!r}; expected 'refuse' or 'combined'"
        )
    combined_values = set()
    for entry in cfg.get("categorize.combined_values") or []:
        when = (entry or {}).get("when") or []
        if len(when) < 2:
            out.append(f"categorize.combined_values entry needs >=2 'when' values: {entry!r}")
        for value in when:
            _ref("categorize.combined_values.when", value)
        value = (entry or {}).get("value")
        if not value:
            out.append(f"categorize.combined_values entry has no 'value': {entry!r}")
        else:
            _ref("categorize.combined_values", value)
            combined_values.add(value)
    if combined_values and combine != "combined":
        out.append(
            "categorize.combined_values is populated but categorize.combine is "
            f"{combine!r} — those combined values can never be assigned"
        )

    # Values with no route to being assigned are dead config — usually a typo.
    assignable = set(combined_values)
    for entry in cfg.get("categorize.name_patterns") or []:
        if isinstance(entry, dict) and entry.get("value"):
            assignable.add(entry["value"])
    for key in ("primary_patterns", "generic_primary_patterns", "category_patterns"):
        assignable.update((cfg.get(f"categorize.{key}") or {}).values())
    for value in sorted(values - assignable):
        out.append(
            f"categorize.values contains {value!r} but no pattern can ever assign it"
        )

    missing_spec = values - set(cfg.get("categorize.specificity") or [])
    if missing_spec:
        out.append(
            "categorize.specificity does not rank: "
            + ", ".join(sorted(missing_spec))
            + " — within-family ties involving these resolve arbitrarily"
        )
    return out


_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")


def guaranteed_fields(cfg: Config) -> set:
    """Fields every exported lead is certain to carry.

    A lead has no route to the export without a phone, and without a company
    name either when `export.require_company_name` is on. A sequence may merge
    these without declaring them, and a sequence requiring only these is a
    catch-all — every lead qualifies for it by construction.
    """
    fields = {"phone"}
    if cfg.get("export.require_company_name", True):
        fields.add("company")
    return fields


def _check_messages(cfg: Config) -> list[str]:
    out = []
    available = set(cfg.get("messages.merge_fields") or [])
    guaranteed = guaranteed_fields(cfg)
    sequences = cfg.get("messages.sequences") or []
    if not sequences:
        return ["messages.sequences is empty — there is nothing to send"]

    for si, seq in enumerate(sequences):
        if not isinstance(seq, dict):
            out.append(f"messages.sequences[{si}] must be a mapping")
            continue
        name = seq.get("name") or f"#{si}"
        requires = set(seq.get("requires") or [])
        for field in sorted(requires - available):
            out.append(
                f"sequence {name!r} requires {field!r}, which is not in messages.merge_fields"
            )
        msgs = seq.get("messages") or []
        if not msgs:
            out.append(f"sequence {name!r} has no messages")
        for mi, msg in enumerate(msgs):
            body = (msg or {}).get("body") or ""
            if not body.strip():
                out.append(f"sequence {name!r} message {mi} has an empty body")
            used = set(_PLACEHOLDER_RE.findall(body))
            for field in sorted(used - available):
                out.append(
                    f"sequence {name!r} message {mi} uses {{{field}}}, "
                    f"which is not in messages.merge_fields"
                )
            # The structural guarantee: a sequence may only merge fields it
            # declared as required, or fields every lead is guaranteed to
            # have. Otherwise a lead without the field enters the sequence and
            # renders a hole.
            for field in sorted(used - requires - guaranteed):
                if field in available:
                    out.append(
                        f"sequence {name!r} message {mi} uses {{{field}}} but does not "
                        f"list it in `requires` — a lead missing that field would be "
                        f"sent a message with a blank in it"
                    )

    # Without a catch-all, leads missing every optional field match no
    # sequence and are silently skipped at export.
    has_catch_all = any(
        set(s.get("requires") or []) <= guaranteed
        for s in sequences if isinstance(s, dict)
    )
    if not has_catch_all:
        out.append(
            "no catch-all sequence: every sequence requires an optional field, so "
            "leads that have none of them will match nothing and be skipped at "
            "export. Add a sequence whose `requires` lists only "
            + ", ".join(sorted(guaranteed))
            + " (or nothing at all)."
        )
    return out


def _check_scoring_invariant(cfg: Config) -> list[str]:
    """A bare category match must not be exportable. See score_report()."""
    report = score_report(cfg)
    if report["bare_category_exports"]:
        return [
            "SCORING INVARIANT BROKEN: a listing whose only signal is a core "
            f"category scores {report['bare_category_score']}, at or above the "
            f"export threshold of {report['threshold']}. Every business your deny "
            "list failed to catch that happens to carry a core category will "
            "export. Raise score.export_threshold above "
            f"{report['bare_category_score']}, or lower "
            "score.weights.core_primary_category."
        ]
    if report["weak_only_exports"]:
        return [
            "SCORING INVARIANT BROKEN: a listing whose only identity signal is a "
            f"WEAK name word scores {report['weak_only_score']} with every soft "
            f"bonus applied, at or above the export threshold of "
            f"{report['threshold']}. Lower score.weights.weak_name_signal."
        ]
    return []


def score_report(cfg: Config) -> dict:
    """Compute the two ceiling cases the invariant depends on.

    bare category: a core primary category and nothing else. Bare-ness implies
    no website and no reviews, so the negative bonus applies.

    weak only: a weak name word plus EVERY soft bonus (reviews, rating,
    website) but no core category and no strong name signal.
    """
    w = cfg.get("score.weights") or {}
    threshold = int(cfg.get("score.export_threshold", 50))

    def weight(key: str, default: int = 0) -> int:
        return int(w.get(key, default))

    bare = weight("core_primary_category") + weight("no_website_no_reviews")
    weak = (
        weight("weak_name_signal")
        + weight("reviews_in_band")
        + weight("rating_at_or_above")
        + weight("has_website")
    )
    return {
        "threshold": threshold,
        "bare_category_score": bare,
        "bare_category_exports": bare >= threshold,
        "bare_category_no_penalty": weight("core_primary_category"),
        "weak_only_score": weak,
        "weak_only_exports": weak >= threshold,
    }


def _check_compliance(cfg: Config) -> list[str]:
    out = []
    qh = cfg.get("compliance.quiet_hours") or {}
    for field in ("earliest", "latest"):
        val = str(qh.get(field, ""))
        if not re.fullmatch(r"\d{2}:\d{2}", val):
            out.append(f"compliance.quiet_hours.{field} is {val!r}; expected HH:MM")
    source = qh.get("timezone_source", "lead_state")
    if source not in ("lead_state", "fixed"):
        out.append(
            f"compliance.quiet_hours.timezone_source is {source!r}; "
            "expected 'lead_state' or 'fixed'"
        )
    if source == "lead_state" and not (qh.get("state_timezones") or {}):
        out.append(
            "compliance.quiet_hours.timezone_source is 'lead_state' but "
            "state_timezones is empty"
        )
    mode = cfg.get("compliance.verification.mode", "none")
    if mode not in ("none", "crm", "webhook"):
        out.append(
            f"compliance.verification.mode is {mode!r}; expected none|crm|webhook"
        )
    if mode == "webhook" and not os.environ.get("LEADPIPE_LINETYPE_URL"):
        out.append(
            "compliance.verification.mode is 'webhook' but LEADPIPE_LINETYPE_URL "
            "is not set in the environment"
        )
    return out
