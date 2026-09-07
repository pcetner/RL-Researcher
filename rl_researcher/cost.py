"""What a run will cost, from what runs of its shape have cost before.

The estimate is units still to run times the seconds one unit took on this device, read from
``<ledger>/throughput.jsonl``, which the runner appends to after every unit. That makes the
estimator calibrated by the same principle as any instrument: a number nobody has measured is
an assumption. When this device has no record the estimate says so (``basis``) and the gate
treats it conservatively; running the canary on a new device is what populates the table.
"""

from __future__ import annotations

import json
import platform
import shutil
import statistics
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from rl_researcher.config import Config
from rl_researcher.kinds import DeviceInfo, RunKind
from rl_researcher.spec import RunSpec, spec_fingerprint
from rl_researcher.units import RESULTS_NAME, unit_dir

THROUGHPUT_NAME = "throughput.jsonl"
SAMPLES = 5


@dataclass
class Cost:
    run: str
    kind: str
    fingerprint: str
    device: DeviceInfo
    units: int
    unit_class: str
    seconds_per_unit: Optional[float]
    basis: str                 # "measured" | "assumed-slowest" | "budget-cap" | "nothing-to-run"
    samples: int
    wall_seconds: float
    money_usd: float
    new_data_minutes: float = 0.0
    tags: List[str] = field(default_factory=list)
    gated: bool = False
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["device"] = asdict(self.device)
        return d

    def describe(self) -> str:
        wall = self.wall_seconds
        if wall >= 3600:
            w = f"{wall / 3600:.1f} h"
        elif wall >= 60:
            w = f"{wall / 60:.0f} min"
        else:
            w = f"{wall:.0f} s"
        per = "?" if self.seconds_per_unit is None else f"{self.seconds_per_unit:.0f} s"
        line = (f"{self.units} unit(s) x {per} on {self.device.name} = {w}, ${self.money_usd:.2f} "
                f"[{self.basis}, {self.samples} sample(s)]")
        if self.new_data_minutes:
            line += f", new data {self.new_data_minutes:.0f} min"
        return line


def throughput_path(config: Config) -> Path:
    return config.path("ledger") / THROUGHPUT_NAME


def read_throughput(config: Config) -> List[Dict[str, Any]]:
    p = throughput_path(config)
    if not p.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def append_throughput(config: Config, row: Dict[str, Any]) -> None:
    p = throughput_path(config)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def record_throughput(config: Config, kind: RunKind, spec: RunSpec, unit: str, result: Dict[str, Any],
                      device: Optional[DeviceInfo]) -> None:
    """One row per finished unit: seconds per unit and per step on this device."""
    steps = int(result.get("steps") or 0)
    seconds = float(result.get("seconds") or 0.0)
    if seconds <= 0:
        return
    append_throughput(config, {
        "kind": kind.name, "unit_class": kind.unit_class(spec, unit),
        "device": device.fingerprint if device else "unknown",
        "device_name": device.name if device else "unknown",
        "seconds_per_unit": round(seconds, 1),
        "seconds_per_step": round(seconds / steps, 6) if steps else None,
        "steps": steps, "status": result.get("status"), "run": spec.name, "unit": unit,
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })


def _gpu_name() -> Optional[str]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=name", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        return None
    return out[0].strip() if out else None


def probe_device(kind: RunKind, config: Optional[Config] = None) -> DeviceInfo:
    """The device this process would run on. The kind may know better (it holds torch); the
    framework falls back to ``nvidia-smi``, then the CPU. The config supplies cost and marks
    the device as known."""
    info = kind.device()
    if info is None:
        gpu = _gpu_name()
        name = gpu or platform.processor() or platform.machine() or "cpu"
        info = DeviceInfo(name=name, kind="cuda" if gpu else "cpu")
    fp = info.fingerprint or f"{platform.node()}/{info.name}"
    dc = config.device_config(info.name) if config is not None else None
    if dc is not None:
        return DeviceInfo(name=info.name, kind=dc.kind, fingerprint=fp, hourly_usd=dc.hourly_usd, known=True)
    return DeviceInfo(name=info.name, kind=info.kind, fingerprint=fp, hourly_usd=info.hourly_usd, known=info.known)


def _median_recent(rows: List[Dict[str, Any]], key: str) -> Optional[float]:
    vals = [float(r[key]) for r in rows[-SAMPLES:] if r.get(key) is not None]
    return statistics.median(vals) if vals else None


def estimate(spec: RunSpec, kind: RunKind, config: Config, *, out: Optional[Path] = None,
             max_steps: Optional[int] = None, max_seconds: Optional[float] = None,
             device: Optional[DeviceInfo] = None, units: Optional[List[str]] = None) -> Cost:
    """The cost of running what ``spec`` has left to run."""
    device = device or probe_device(kind, config)
    max_steps = max_steps or spec.budget.max_steps
    max_seconds = max_seconds if max_seconds is not None else spec.budget.max_seconds
    todo = list(kind.units(spec))
    if units:
        wanted = set(units)
        todo = [u for u in todo if u in wanted or u.split("/seed", 1)[0] in wanted]
    if out is not None:
        todo = [u for u in todo if not (unit_dir(Path(out), u) / RESULTS_NAME).is_file()]
    unit_class = kind.unit_class(spec, todo[0]) if todo else kind.unit_class(spec, kind.units(spec)[0])
    rows = [r for r in read_throughput(config) if r.get("kind") == kind.name and r.get("unit_class") == unit_class]
    here = [r for r in rows if r.get("device") == device.fingerprint]
    per_step_here = _median_recent(here, "seconds_per_step")
    per_unit_here = _median_recent(here, "seconds_per_unit")
    samples = 0
    if per_step_here is not None or per_unit_here is not None:
        spu = per_step_here * max_steps if per_step_here is not None else float(per_unit_here or 0.0)
        basis, samples = "measured", len(here[-SAMPLES:])
    elif rows and config.gate.unknown_device == "assume-slowest":
        slowest = max(float(r.get("seconds_per_step") or 0) * max_steps if r.get("seconds_per_step")
                      else float(r.get("seconds_per_unit") or 0) for r in rows)
        spu = slowest * config.gate.unknown_device_factor
        basis, samples = "assumed-slowest", len(rows)
    else:
        spu = float(max_seconds)
        basis = "budget-cap"
    spu = min(spu, float(max_seconds))
    if not todo:
        basis, spu = "nothing-to-run", 0.0
    wall = spu * len(todo)
    new_data = float(getattr(kind, "new_data_minutes", lambda s: 0.0)(spec) or 0.0)
    tags = list(getattr(kind, "gate_tags", lambda s: [])(spec) or [])
    return Cost(run=spec.name, kind=kind.name, fingerprint=spec_fingerprint(spec), device=device,
                units=len(todo), unit_class=unit_class, seconds_per_unit=spu if todo else None, basis=basis,
                samples=samples, wall_seconds=wall, money_usd=wall / 3600.0 * device.hourly_usd,
                new_data_minutes=new_data, tags=tags)
