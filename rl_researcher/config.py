"""The consuming project's configuration: ``rl-researcher.toml`` at its root.

Found by walking upward from the working directory. It names the run kinds, where each kind's
outputs live, the gate thresholds, the devices and their hourly cost, the watcher's cadence and
the canary. Everything has a default, so a minimal file is ``[project] name = "x"`` plus a
``[kinds]`` table.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

from rl_researcher.kinds import RunKind, load_kind
from rl_researcher.spec import RunSpec, kind_of, load_toml

CONFIG_NAME = "rl-researcher.toml"

BUILTIN_KINDS = {"toy": "rl_researcher.examples.toy.kind:ToyKind"}

DEFAULT_OUT = {"study": "docs/studies", "engine-loop": "docs/loops", "measurement": "docs/measurements",
               "toy": "docs/toy"}


def _dc(cls, d: Dict[str, Any]):
    known = {f.name for f in fields(cls)}
    unknown = set(d) - known
    if unknown:
        raise ValueError(f"{CONFIG_NAME}: [{cls.__name__.replace('Config', '').lower()}] unknown keys "
                         f"{sorted(unknown)}")
    return cls(**d)


@dataclass
class GateConfig:
    ungated_wall_minutes: float = 45.0
    ungated_money_usd: float = 0.0
    ungated_new_data_minutes: float = 20.0
    unknown_device: str = "gate"          # "gate" | "assume-slowest"
    unknown_device_factor: float = 2.0
    always_gated: List[str] = field(default_factory=lambda: ["cloud", "full-snapshot"])
    screening_seeds: int = 1


@dataclass
class DeviceConfig:
    hourly_usd: float = 0.0
    kind: str = "cpu"


@dataclass
class PathsConfig:
    specs: str = "studies"
    ledger: str = "docs/ledger"
    state: str = "docs"
    plan: str = "docs/PLAN.md"
    lessons: str = "docs/lessons.md"
    logs: str = "docs/measurements/logs"
    approvals: str = "studies/approvals"
    queue: str = "studies/queue.toml"
    diagnoses: str = "docs/diagnoses"


@dataclass
class WatcherConfig:
    interval_seconds: float = 60.0
    launch: bool = False
    notify: str = "toast"
    stale_factor: float = 2.0
    eta_overrun: float = 0.5


@dataclass
class CanaryConfig:
    spec: Optional[str] = None
    watched: List[str] = field(default_factory=list)


@dataclass
class Config:
    root: Path
    name: str = "project"
    python: str = "python"
    goal: str = ""
    focus: str = ""
    kinds: Dict[str, str] = field(default_factory=dict)
    out: Dict[str, str] = field(default_factory=dict)
    gate: GateConfig = field(default_factory=GateConfig)
    devices: Dict[str, DeviceConfig] = field(default_factory=dict)
    paths: PathsConfig = field(default_factory=PathsConfig)
    watcher: WatcherConfig = field(default_factory=WatcherConfig)
    canary: CanaryConfig = field(default_factory=CanaryConfig)
    source: Optional[Path] = None

    def path(self, key: str) -> Path:
        return self.root / getattr(self.paths, key)

    def out_root(self, kind: str) -> Path:
        return self.root / self.out.get(kind, DEFAULT_OUT.get(kind, f"docs/{kind}"))

    def kind_entry(self, kind: str) -> str:
        entry = self.kinds.get(kind) or BUILTIN_KINDS.get(kind)
        if not entry:
            raise KeyError(f"no run kind {kind!r} in {CONFIG_NAME} [kinds] (known: "
                           f"{sorted(set(self.kinds) | set(BUILTIN_KINDS))})")
        return entry

    def device_config(self, name: str) -> Optional[DeviceConfig]:
        return self.devices.get(name)


def config_from_dict(d: Dict[str, Any], root: Path, source: Optional[Path] = None) -> Config:
    project = dict(d.get("project", {}))
    return Config(
        root=Path(root),
        name=str(project.get("name", root.name)),
        python=str(project.get("python", "python")),
        goal=str(project.get("goal", "")),
        focus=str(project.get("focus", "")),
        kinds={str(k): str(v) for k, v in dict(d.get("kinds", {})).items()},
        out={str(k): str(v) for k, v in dict(d.get("out", {})).items()},
        gate=_dc(GateConfig, dict(d.get("gate", {}))),
        devices={str(k): _dc(DeviceConfig, dict(v)) for k, v in dict(d.get("devices", {})).items()},
        paths=_dc(PathsConfig, dict(d.get("paths", {}))),
        watcher=_dc(WatcherConfig, dict(d.get("watcher", {}))),
        canary=_dc(CanaryConfig, dict(d.get("canary", {}))),
        source=source,
    )


def find_config_file(start: Optional[Path] = None) -> Optional[Path]:
    here = Path(start or os.getcwd()).resolve()
    for p in (here, *here.parents):
        candidate = p / CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def load_config(start: Optional[Path] = None, *, required: bool = True) -> Config:
    """The nearest ``rl-researcher.toml`` at or above ``start`` (the working directory)."""
    path = find_config_file(start)
    if path is None:
        if required:
            raise FileNotFoundError(f"no {CONFIG_NAME} found at or above {Path(start or os.getcwd()).resolve()}; "
                                    f"run from inside a project, or create one")
        return Config(root=Path(start or os.getcwd()).resolve())
    return config_from_dict(load_toml(path), root=path.parent, source=path)


def kind_for(spec_path: "str | Path", config: Config) -> RunKind:
    """The kind object a spec file names (``kind = "study"``; default ``study``)."""
    return load_kind(config.kind_entry(kind_of(spec_path)))


def out_dir_for(spec: RunSpec, config: Config) -> Path:
    return config.out_root(spec.kind) / spec.name


def resolve_spec(path: "str | Path", config: Config) -> Path:
    """A spec given by path, or by name inside the project's specs directory."""
    p = Path(path)
    if p.is_file():
        return p
    alt = config.path("specs") / (p.name if p.suffix == ".toml" else f"{p.name}.toml")
    if alt.is_file():
        return alt
    raise FileNotFoundError(f"no spec at {p} or {alt}")
