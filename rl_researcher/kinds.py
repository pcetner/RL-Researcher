"""The run-kind protocol: what a consuming project supplies for each kind of run.

A run kind says what its units are and how to run one. The framework supplies everything
around that: the spec, the lock, the heartbeat, the checkpoint bookkeeping, the log, the status,
the cost estimate, the gate, the artefacts and the ledger. A kind is a plain object; it is
named in the project's ``rl-researcher.toml`` as ``"module:Class"`` and instantiated with no
arguments.
"""

from __future__ import annotations

import importlib
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import (Any, Callable, Dict, Generic, List, Optional, Protocol, Sequence, TypeVar,
                    runtime_checkable)

from rl_researcher.checkpoint import write_sidecar
from rl_researcher.spec import CadenceSpec, MetricRegistry, RunSpec
from rl_researcher.stop import stop_requested
from rl_researcher.units import thin, write_progress

Log = Callable[[str], None]

#: The spec type a kind reads.
#:
#: A kind with structure of its own is told, in `docs/run-kinds.md`, to declare a `RunSpec`
#: subclass with real fields — so that a mistake is caught at load time rather than as a
#: `KeyError` an hour into a run. Its `units(spec: StudySpec)` then narrows the parameter its
#: base declares, which is a Liskov violation and which every type checker reports as one.
#:
#: The protocol is parameterised instead, so the advice the documentation gives is the advice
#: the types express: a kind is a `RunKind[StudySpec]`, and the framework, which does not care
#: which spec a kind reads, asks for `RunKind[Any]`.
SpecT = TypeVar("SpecT", bound=RunSpec)


@dataclass(frozen=True)
class DeviceInfo:
    """What a unit runs on, for throughput records and cost. ``fingerprint`` is
    ``hostname/device name``; ``known`` is whether the project's config lists the device."""

    name: str
    kind: str = "cpu"        # "cuda" | "cpu" | "cloud"
    fingerprint: str = ""
    hourly_usd: float = 0.0
    known: bool = False


@dataclass(frozen=True)
class CurveSpec:
    """A history series a dashboard draws for a running unit: the key in ``history``, a short
    title, and optionally the metric whose registered bar is its floor."""

    key: str
    title: str
    floor_metric: Optional[str] = None


@dataclass(frozen=True)
class Guard:
    """A condition under which a run must not start (the GPU is shared with a game engine,
    say). ``is_blocked`` is asked at launch; ``message`` is what the refusal says."""

    name: str
    is_blocked: Callable[[], bool]
    message: str


@dataclass
class LogVocab:
    """How a log tail is coloured: substrings that mark a notable line and the tone they get,
    and the regex of a routine step line so bursts of them can be thinned."""

    marks: Dict[str, str] = field(default_factory=dict)
    step_line: Optional[str] = None


@dataclass
class Finding:
    check: str
    level: str      # "error" | "warn"
    message: str


@dataclass
class UnitResult:
    """What one unit produced. ``metrics`` are the registered numbers by name; ``history`` the
    series the dashboard drew; ``extras`` non-scalar evidence; ``evidence`` image paths relative
    to the run directory; ``extra`` anything else the kind wants in the result."""

    arm: str
    seed: int
    status: str                  # "complete" | "incomplete"
    steps: int
    max_steps: int
    seconds: float
    metrics: Dict[str, float]
    history: Dict[str, List[float]] = field(default_factory=dict)
    extras: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, str] = field(default_factory=dict)
    params: int = 0
    resumed_from_step: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def unit(self) -> str:
        return f"{self.arm}/seed{self.seed}"

    def to_dict(self) -> Dict[str, Any]:
        """The unit's ``results.json``. ``extra`` is merged in at the top level rather than
        nested, so a kind can add the fields its own writers already read (a study's
        ``variant`` and ``arch``, say) without the readers learning a new shape. The declared
        fields win a collision: a kind cannot overwrite the contract from ``extra``."""
        d = asdict(self)
        extra = d.pop("extra", None) or {}
        merged: Dict[str, Any] = dict(extra)
        merged.update(d)
        merged["unit"] = self.unit
        return merged


@dataclass
class RunContext:
    """What the framework hands a kind for the run as a whole."""

    out: Path
    log: Log
    device: Optional[DeviceInfo]
    max_steps: int
    max_seconds: float
    config: Any = None
    selected: Optional[List[str]] = None   # unit ids this machine runs; None = all
    #: The commit that produced this run's units. On a rebuild — every unit already finished —
    #: it is the commit the earlier summary recorded, not today's HEAD. A kind that stamps
    #: provenance must take it from here rather than asking git, or a regenerated document
    #: attributes measurements to code that did not make them.
    commit: str = ""
    #: Which version of this package produced them, under the same rule.
    framework: str = ""
    #: The summary this run is replacing, if there is one. On a rebuild nothing was prepared,
    #: so anything ``summarise`` would have taken from ``prepare`` — the data split, a snapshot
    #: manifest — is not available to recompute and must be carried forward from here.
    previous: Dict[str, Any] = field(default_factory=dict)


@dataclass
class UnitContext:
    """What the framework hands a kind for one unit: where to write, how to beat, when to stop."""

    unit: str
    arm: str
    seed: int
    cell: Path
    progress: Path
    log: Log
    device: Optional[DeviceInfo]
    max_steps: int
    max_seconds: float
    resume: bool
    fingerprint: str
    cadence: CadenceSpec
    on_beat: Optional[Callable[[], None]] = None
    started: float = field(default_factory=time.time)

    def beat(self, status: str, *, step: int, elapsed_seconds: float, rate: Optional[float] = None,
             eta_seconds: Optional[float] = None, last: Optional[Dict[str, float]] = None,
             history: Optional[Dict[str, List[float]]] = None, **extra: Any) -> None:
        """Write the heartbeat and let the dashboard redraw. Called at least every
        ``cadence.heartbeat_seconds`` while the unit is alive, and on every state change."""
        fields: Dict[str, Any] = dict(
            unit=self.unit, arm=self.arm, seed=self.seed, status=status, step=int(step),
            max_steps=int(self.max_steps), elapsed_seconds=round(float(elapsed_seconds), 1),
            rate=None if rate is None else round(float(rate), 3),
            eta_seconds=None if eta_seconds is None else round(float(eta_seconds)),
            budget_seconds=self.max_seconds,
            device=self.device.name if self.device else None,
            last=dict(last or {}),
            history={k: thin(v) for k, v in (history or {}).items() if v},
        )
        fields.update(extra)
        write_progress(self.progress, **fields)
        if self.on_beat is not None:
            self.on_beat()

    def sidecar(self, *, step: int, elapsed_seconds: float) -> str:
        """Record that a checkpoint exists at ``step`` (the payload is the kind's own)."""
        return write_sidecar(self.cell, step=step, elapsed_seconds=elapsed_seconds)

    def stop_requested(self) -> bool:
        return stop_requested()


@runtime_checkable
class RunKind(Protocol[SpecT]):
    """What a kind implements. Only ``units``, ``load``, ``run_unit`` and ``registry`` are
    needed for a run; the rest have working defaults through :class:`BaseKind`."""

    name: str
    registry: MetricRegistry

    def load(self, path: Path) -> SpecT: ...
    def units(self, spec: SpecT) -> List[str]: ...
    def unit_class(self, spec: SpecT, unit: str) -> str: ...
    def device(self) -> Optional[DeviceInfo]: ...
    def guards(self, spec: SpecT) -> List[Guard]: ...
    def check(self, spec: SpecT, ctx: Any) -> List[Finding]: ...
    def pin(self, spec: SpecT, path: Path) -> Optional[str]: ...
    def prepare(self, spec: SpecT, ctx: RunContext) -> Any: ...
    def run_unit(self, spec: SpecT, unit: str, prepared: Any, ctx: UnitContext) -> UnitResult: ...
    def read_result(self, result: Dict[str, Any]) -> Dict[str, Any]: ...
    def summarise(self, spec: SpecT, results: List[Dict[str, Any]], out: Path, ctx: RunContext) -> Dict[str, Any]: ...
    def curves(self, spec: SpecT) -> List[CurveSpec]: ...
    def log_vocab(self) -> LogVocab: ...
    def blocks(self, spec: SpecT, summary: Dict[str, Any], out: Path, view: str) -> List[Any]: ...
    def instrument_for(self, metric: str) -> Optional[str]: ...
    def estimator_name(self, metric: str) -> str: ...


class BaseKind(Generic[SpecT]):
    """Defaults for everything a kind need not customise. Subclass it, set ``name`` and
    ``registry``, and implement ``units``, ``run_unit`` and, usually, ``load``."""

    name: str = "base"
    registry: MetricRegistry = MetricRegistry(known={})
    log_name: str = "run.log"

    #: Whether a difference this kind reports is read across seeds. True for anything with
    #: training dynamics; False for a run that is deterministic given its data, where a second
    #: seed would produce the same number and the checks should not ask for one.
    compares_seeds: bool = True

    def load(self, path: Path) -> SpecT:
        """The generic loader, which builds a plain :class:`RunSpec`.

        A kind that declares a spec type of its own overrides this and returns that type; the
        cast is what says so. It is the one place the parameter cannot be honoured by the
        default, because a default cannot know the subclass a kind chose.
        """
        from typing import cast

        from rl_researcher.spec import load_run_spec

        return cast(SpecT, load_run_spec(path, registry=self.registry, default_kind=self.name))

    def units(self, spec: SpecT) -> List[str]:
        raise NotImplementedError

    def arms(self, spec: SpecT) -> List[str]:
        seen: List[str] = []
        for u in self.units(spec):
            arm = u.split("/seed", 1)[0]
            if arm not in seen:
                seen.append(arm)
        return seen

    def unit_class(self, spec: SpecT, unit: str) -> str:
        return f"{self.name}/{spec.budget.max_steps}steps"

    def device(self) -> Optional[DeviceInfo]:
        return None

    def guards(self, spec: SpecT) -> List[Guard]:
        return []

    def check(self, spec: SpecT, ctx: Any) -> List[Finding]:
        return []

    def pin(self, spec: SpecT, path: Path) -> Optional[str]:
        return None

    def prepare(self, spec: SpecT, ctx: RunContext) -> Any:
        return None

    def run_unit(self, spec: SpecT, unit: str, prepared: Any, ctx: UnitContext) -> UnitResult:
        raise NotImplementedError

    def read_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """One unit's ``results.json`` in the framework's shape.

        The identity for a kind whose runs were always written by this framework. A kind whose
        finished runs predate it overrides this to translate on read: those files are evidence,
        and rewriting evidence so a newer tool can parse it is exactly what the ledger exists
        to make impossible.
        """
        return result

    def summarise(self, spec: SpecT, results: List[Dict[str, Any]], out: Path, ctx: RunContext) -> Dict[str, Any]:
        return {}

    def curves(self, spec: SpecT) -> List[CurveSpec]:
        return []

    def log_vocab(self) -> LogVocab:
        return LogVocab(marks={"FAILED": "crit", "resumed": "warn", "stale lock": "warn",
                               "report ->": "ok", "complete": "ok"},
                        step_line=r"^\d\d:\d\d:\d\d \[(?P<unit>[^\]]+)\] step\s+(?P<step>\d+)/(?P<of>\d+)")

    def blocks(self, spec: SpecT, summary: Dict[str, Any], out: Path, view: str) -> List[Any]:
        return []

    def instrument_for(self, metric: str) -> Optional[str]:
        return None

    def estimator_name(self, metric: str) -> str:
        return f"{type(self).__module__}.{type(self).__name__}"


def load_kind(entry: str) -> "RunKind[Any]":
    """Instantiate ``"package.module:ClassName"``."""
    if ":" not in entry:
        raise ValueError(f"a kind entry is 'module:Class', got {entry!r}")
    mod_name, cls_name = entry.split(":", 1)
    cls = getattr(importlib.import_module(mod_name), cls_name)
    kind = cls()
    if not isinstance(kind, RunKind):
        raise TypeError(f"{entry} does not implement RunKind")
    return kind


def units_from(arms: Sequence[str], seeds: Sequence[int]) -> List[str]:
    """The conventional unit order: every seed of the first arm, then the next arm."""
    return [f"{a}/seed{int(s)}" for a in arms for s in seeds]
