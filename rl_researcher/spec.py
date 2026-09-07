"""The pre-registered part of a run, loaded from TOML.

A spec is what a run commits to before it starts: the hypothesis, the seeds, the metrics with
their bars, the budget, the heartbeat and checkpoint cadence, and which decisions of the
project's plan it bears on. ``spec_fingerprint`` is the identity of that registered part, so a
bar edited after a result is a different spec, and an approval or a checkpoint bound to the old
fingerprint no longer applies.

The framework knows only the generic keys. A run kind loads the same TOML and keeps its own
tables (a study's snapshot and variants, an engine loop's model and arms) in ``RunSpec.extra``
or in a subclass; either way they are part of the fingerprint.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on the 3.10 CI leg
    import tomli as tomllib  # type: ignore[no-redef]

GENERIC_KEYS = ("name", "kind", "hypothesis", "seeds", "metrics", "budget", "cadence",
                "decision_touches", "screening", "conjunction")


class SpecError(ValueError):
    pass


@dataclass
class MetricRegistry:
    """What a project can measure: every metric name the runner computes, with a definition
    a reader can use, an optional formula in mathtext, and a human title.

    A spec that registers a name not in ``known`` is refused at load time: the registration
    must refer to something that will actually be measured.
    """

    known: Dict[str, str]
    formulas: Dict[str, str] = field(default_factory=dict)
    titles: Dict[str, str] = field(default_factory=dict)

    def __contains__(self, name: object) -> bool:
        return name in self.known

    def description(self, name: str) -> str:
        return self.known.get(name, "")

    def formula(self, name: str) -> str:
        return self.formulas.get(name, "")

    def title(self, name: str) -> str:
        return self.titles.get(name) or name.replace("_", " ").capitalize()


@dataclass
class MetricSpec:
    name: str
    baseline: str = ""
    direction: str = "report"  # "lower" | "higher" | "report"
    bar: Optional[float] = None
    why: str = ""
    compare_to: Optional[str] = None  # judge against this arm's mean in the same run
    anchor_of: Optional[str] = None   # "<run>/<arm>": this metric re-runs an earlier cell

    def validate(self, registry: Optional[MetricRegistry] = None) -> None:
        if registry is not None and self.name not in registry:
            raise SpecError(f"unknown metric {self.name!r}; known: {sorted(registry.known)}")
        if self.direction not in ("lower", "higher", "report"):
            raise SpecError(f"metric {self.name}: direction must be lower|higher|report")
        if self.direction == "report" and (self.bar is not None or self.compare_to):
            raise SpecError(f"metric {self.name}: a 'report' metric has no bar and no compare_to")
        if self.bar is not None and self.compare_to:
            raise SpecError(f"metric {self.name}: a bar and a compare_to are two different tests; "
                            "register one of them")


@dataclass
class BudgetSpec:
    max_steps: int = 20000
    max_seconds: float = 8 * 3600.0


@dataclass
class CadenceSpec:
    """How often a unit must show it is alive and how often it must be resumable. Neither can
    be disabled: a run that is silent for more than five minutes is indistinguishable from a
    hung one, and a run that cannot resume loses a night to a shutdown."""

    heartbeat_seconds: float = 60.0
    checkpoint_every_steps: int = 500
    checkpoint_seconds: float = 600.0

    def validate(self) -> None:
        if not 1.0 <= self.heartbeat_seconds <= 300.0:
            raise SpecError("cadence.heartbeat_seconds must be in [1, 300]: a run must report "
                            "progress at least every five minutes (it cannot be disabled)")
        if self.checkpoint_every_steps < 1 or self.checkpoint_seconds < 1.0:
            raise SpecError("cadence.checkpoint_every_steps must be >= 1 and checkpoint_seconds "
                            ">= 1: a unit must be resumable after a shutdown (it cannot be disabled)")


@dataclass
class RunSpec:
    name: str
    kind: str
    hypothesis: str
    seeds: List[int]
    metrics: List[MetricSpec]
    budget: BudgetSpec = field(default_factory=BudgetSpec)
    cadence: CadenceSpec = field(default_factory=CadenceSpec)
    decision_touches: List[str] = field(default_factory=list)
    screening: bool = False            # one seed is allowed; the report says so
    conjunction: List[str] = field(default_factory=list)  # bars an arm must clear together
    extra: Dict[str, Any] = field(default_factory=dict)   # the kind's own tables, verbatim
    source_path: Optional[str] = None

    @property
    def primary(self) -> Optional[MetricSpec]:
        """The first metric with a bar or a comparison; what a headline is about."""
        for m in self.metrics:
            if m.bar is not None or m.compare_to:
                return m
        return None

    def metric(self, name: str) -> Optional[MetricSpec]:
        return next((m for m in self.metrics if m.name == name), None)

    def validate(self, registry: Optional[MetricRegistry] = None) -> "RunSpec":
        if not self.name:
            raise SpecError("a run needs a name")
        if not self.kind:
            raise SpecError("a run needs a kind")
        if not self.hypothesis.strip():
            raise SpecError("a run needs a hypothesis (pre-registration is the point)")
        if not self.seeds:
            raise SpecError("a run needs at least one seed")
        if len(set(self.seeds)) != len(self.seeds):
            raise SpecError("seeds must be distinct")
        if not self.metrics:
            raise SpecError("a run needs pre-registered metrics")
        names = [m.name for m in self.metrics]
        if len(set(names)) != len(names):
            raise SpecError("metric names must be unique")
        for m in self.metrics:
            m.validate(registry)
        for c in self.conjunction:
            cm = self.metric(c)
            if cm is None or (cm.bar is None and not cm.compare_to):
                raise SpecError(f"conjunction names {c!r}, which is not a registered metric with a "
                                "bar or a comparison")
        if self.budget.max_steps < 1:
            raise SpecError("budget.max_steps must be >= 1")
        if self.budget.max_seconds < 0:
            # Zero is allowed and means "stop at the first check": it is how a run proves it
            # reports a time cap as *incomplete* rather than as a result. A negative cap is a
            # typo, and would make every unit incomplete without saying why.
            raise SpecError("budget.max_seconds must be >= 0")
        self.cadence.validate()
        return self


def load_toml(path: "str | Path") -> Dict[str, Any]:
    with Path(path).open("rb") as fh:
        return tomllib.load(fh)


def dc(cls, d: Dict[str, Any]):
    """Build a dataclass from a dict, refusing keys it does not declare: a typo must not
    silently run the default."""
    known = {f.name for f in fields(cls)}
    unknown = set(d) - known
    if unknown:
        raise SpecError(f"{cls.__name__}: unknown keys {sorted(unknown)}")
    return cls(**d)


def without_defaults(obj: Any) -> Any:
    """``asdict`` minus the fields still at their dataclass default, recursively, so a knob
    added to the code with a default does not change the identity of an existing spec."""
    if is_dataclass(obj) and not isinstance(obj, type):
        out: Dict[str, Any] = {}
        for f in fields(obj):
            value = getattr(obj, f.name)
            has_default = f.default is not MISSING or f.default_factory is not MISSING  # type: ignore[misc]
            if has_default:
                default = f.default if f.default is not MISSING else f.default_factory()  # type: ignore[misc]
                if value == default:
                    continue
            out[f.name] = without_defaults(value)
        return out
    if isinstance(obj, list):
        return [without_defaults(v) for v in obj]
    if isinstance(obj, dict):
        return {k: without_defaults(v) for k, v in obj.items()}
    return obj


def spec_fingerprint(spec: Any) -> str:
    """Identity of the registered part of a run: everything in the spec except where it was
    loaded from, and nothing that is merely a default. A checkpoint and an approval both carry
    it, so neither survives an edit to the registration."""
    d = without_defaults(spec)
    d.pop("source_path", None)
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def run_spec_from_dict(d: Dict[str, Any], *, registry: Optional[MetricRegistry] = None,
                       source_path: Optional[str] = None, default_kind: str = "study") -> RunSpec:
    """The generic part of a spec from a TOML dict. Every key the framework does not own goes
    into ``extra`` unchanged, so a kind can read its own tables from there."""
    try:
        budget = dict(d.get("budget", {}))
        cadence = dict(d.get("cadence", {}))
        spec = RunSpec(
            name=str(d["name"]),
            kind=str(d.get("kind", default_kind)),
            hypothesis=str(d.get("hypothesis", "")),
            seeds=[int(s) for s in d.get("seeds", [])],
            metrics=[dc(MetricSpec, dict(m)) for m in d.get("metrics", [])],
            budget=dc(BudgetSpec, budget),
            cadence=dc(CadenceSpec, cadence),
            decision_touches=[str(x) for x in d.get("decision_touches", [])],
            screening=bool(d.get("screening", False)),
            conjunction=[str(x) for x in d.get("conjunction", [])],
            extra={k: v for k, v in d.items() if k not in GENERIC_KEYS},
            source_path=source_path,
        )
    except KeyError as exc:
        raise SpecError(f"spec is missing required key {exc}") from None
    return spec.validate(registry)


def load_run_spec(path: "str | Path", *, registry: Optional[MetricRegistry] = None,
                  default_kind: str = "study") -> RunSpec:
    p = Path(path)
    return run_spec_from_dict(load_toml(p), registry=registry, source_path=str(p),
                              default_kind=default_kind)


def kind_of(path: "str | Path", default: str = "study") -> str:
    """The ``kind`` key of a spec file without validating the rest of it."""
    return str(load_toml(path).get("kind", default))
