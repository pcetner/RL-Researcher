"""One unit of a run, on disk.

A run is a list of units, one per (arm, seed), each in its own directory ``<out>/<arm>/seed<N>/``.
A unit shows it is alive through ``progress.json`` (the heartbeat, rewritten at least every
``cadence.heartbeat_seconds``), proves it finished through ``results.json``, and can be resumed
through a checkpoint whose step is readable from a small ``checkpoint.json`` sidecar. This
module reads and writes those files; it knows nothing about what the unit computes.

Heartbeat timestamps are ISO-8601 UTC. An older file with a float epoch is still readable.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from rl_researcher import atomic

HEARTBEAT_HISTORY_POINTS = 120
PROGRESS_NAME = "progress.json"
RESULTS_NAME = "results.json"

LIVE_STATUSES = ("starting", "resumed", "running", "evaluating")
ENDED_STATUSES = ("done", "incomplete", "stopped", "failed")

_UNIT_RE = re.compile(r"^(?P<arm>[^/]+)/seed(?P<seed>-?\d+)$")


def unit_id(arm: str, seed: int) -> str:
    return f"{arm}/seed{int(seed)}"


def parse_unit(unit: str) -> Tuple[str, int]:
    m = _UNIT_RE.match(unit)
    if not m:
        raise ValueError(f"not a unit id: {unit!r} (expected '<arm>/seed<N>')")
    return m.group("arm"), int(m.group("seed"))


def unit_dir(out: Path, unit: str) -> Path:
    arm, seed = parse_unit(unit)
    return Path(out) / arm / f"seed{seed}"


def stamp_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def age_of(updated: Any) -> Optional[float]:
    """Seconds since a heartbeat's ``updated`` field; ``None`` if it cannot be read.

    Two forms are readable and only one is written. New heartbeats carry an ISO-8601 UTC string;
    runs from before that rule carry a ``time.time()`` float, and those files are still evidence.

    The dispatch is on Python type, never on which parse happens to succeed first. An epoch
    stamped as a JSON *string* is a parse failure, not a heartbeat from 1970 or from fifty years
    hence: reading it as a number would report a live run as decades stale, and reading a real
    number as a string would do the reverse. ``bool`` is excluded because it is an ``int``, and
    ``updated: true`` means a writer got confused, not that the unit beat one second after the
    epoch.
    """
    if updated is None or isinstance(updated, bool):
        return None
    try:
        if isinstance(updated, (int, float)):
            return max(0.0, time.time() - float(updated))
        if not isinstance(updated, str):
            return None
        stamp = datetime.fromisoformat(updated)
        if stamp.tzinfo is None:
            # A stamp with no zone is read as UTC, which is what every writer here emits.
            # Returning None instead would make a slightly-off file indistinguishable from a
            # unit that has never beaten at all, which is the one state that must stay loud.
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


def thin(series: Sequence[float], keep: int = HEARTBEAT_HISTORY_POINTS) -> List[float]:
    """``series`` reduced to at most ``keep`` evenly spaced points, ends included, so a heartbeat
    that carries the training curves stays small however long the unit runs."""
    values = [float(v) for v in series]
    if len(values) <= keep:
        return values
    stride = (len(values) - 1) / (keep - 1)
    return [values[min(int(round(i * stride)), len(values) - 1)] for i in range(keep)]


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_progress(path: Path, **fields: Any) -> None:
    """Atomically write a unit's heartbeat (the hard rule: a run is never silent)."""
    fields.setdefault("updated", stamp_now())
    atomic.write_text(Path(path), json.dumps(fields, indent=2, default=str))


def mark_failed(path: Path, exc: BaseException) -> None:
    """Turn a unit's heartbeat into a failure, keeping whatever it already said (step, rate):
    where it died is most of the diagnosis."""
    fields: Dict[str, Any] = read_json(Path(path)) or {}
    fields.pop("updated", None)
    fields["status"] = "failed"
    fields["error"] = f"{type(exc).__name__}: {exc}"
    try:
        write_progress(Path(path), **fields)
    except OSError:  # pragma: no cover - the log line is the record that matters
        pass


@dataclass
class UnitState:
    """Where one unit stands, read from its files. ``stale`` means the heartbeat is older than
    twice the cadence plus thirty seconds while the unit claims to be alive."""

    unit: str
    arm: str
    seed: int
    status: str = "not started"
    step: Optional[int] = None
    max_steps: Optional[int] = None
    rate: Optional[float] = None
    eta_seconds: Optional[float] = None
    elapsed_seconds: Optional[float] = None
    age: Optional[float] = None
    stale: bool = False
    error: Optional[str] = None
    checkpoint_step: Optional[int] = None
    resumable: bool = False
    resumed_from_step: Optional[int] = None
    progress: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None

    @property
    def done(self) -> bool:
        return self.result is not None

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    @property
    def live(self) -> bool:
        return self.status in LIVE_STATUSES

    def to_dict(self) -> Dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k not in ("progress", "result")}
        d["done"] = self.done
        return d


def read_unit(cell: Path, *, unit: str, heartbeat_seconds: float,
              normalise: Optional[Any] = None) -> UnitState:
    """The state of the unit in ``cell``. A result on disk beats the heartbeat (the result is
    the proof); a failure marker beats staleness; a checkpoint without a result is resumable.

    ``normalise`` is the kind's ``read_result``, given a result dict and returning one in the
    framework's shape. A project whose finished runs predate this contract has files that are
    still evidence and must not be rewritten to suit a newer tool, so the kind that understands
    them translates on read instead.
    """
    arm, seed = parse_unit(unit)
    st = UnitState(unit=unit, arm=arm, seed=seed)
    from rl_researcher.checkpoint import read_sidecar  # local import: checkpoint imports units

    side = read_sidecar(cell)
    if side is not None:
        st.checkpoint_step = int(side.get("step", 0))
    result = read_json(cell / RESULTS_NAME)
    if result is not None and normalise is not None:
        try:
            result = normalise(result)
        except Exception:  # noqa: BLE001 - a kind's translation must never blind the status
            pass
    progress = read_json(cell / PROGRESS_NAME)
    if result is not None:
        st.result = result
        st.status = "done"
        st.step = result.get("steps")
        st.max_steps = result.get("max_steps", result.get("steps"))
        st.elapsed_seconds = result.get("seconds")
        st.resumed_from_step = result.get("resumed_from_step")
        st.progress = progress or {}
        st.age = age_of((progress or {}).get("updated"))
        return st
    if progress is not None:
        st.progress = progress
        st.status = str(progress.get("status", "running"))
        st.step = progress.get("step")
        st.max_steps = progress.get("max_steps")
        st.rate = progress.get("rate", progress.get("steps_per_second"))
        st.eta_seconds = progress.get("eta_seconds")
        st.elapsed_seconds = progress.get("elapsed_seconds")
        st.error = progress.get("error")
        st.resumed_from_step = progress.get("resumed_from_step")
        st.age = age_of(progress.get("updated"))
        if st.status in LIVE_STATUSES:
            st.stale = st.age is None or st.age > 2 * heartbeat_seconds + 30
    st.resumable = st.checkpoint_step is not None
    return st


def stamp_of(updated: Any) -> Optional[datetime]:
    """A heartbeat's ``updated`` field as a UTC datetime; ``None`` if it cannot be read.

    The same two-form dispatch as :func:`age_of`, for the callers that want to *print* when
    something happened rather than how long ago. Without it a reader stringifies whatever the
    file holds, and a run from before the ISO rule reports as ``Finished 1788749820.0716286``.
    """
    if updated is None or isinstance(updated, bool):
        return None
    try:
        if isinstance(updated, (int, float)):
            return datetime.fromtimestamp(float(updated), timezone.utc)
        if not isinstance(updated, str):
            return None
        stamp = datetime.fromisoformat(updated)
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None
