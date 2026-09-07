"""The framework's half of a checkpoint.

What a unit saves to resume from is the kind's business (a study saves model and optimizer
state with torch; the toy kind saves a JSON dict). What the framework needs is small: a
``checkpoint.json`` sidecar with the step and elapsed time so status can be read without
loading the payload, an identity check so a checkpoint from a different spec is never
continued, and the cleanup once the unit has a result.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from rl_researcher import atomic
from rl_researcher.spec import SpecError
from rl_researcher.units import read_json, stamp_now

SIDECAR = "checkpoint.json"
DEFAULT_NAMES: Sequence[str] = ("checkpoint.pt", "checkpoint.json", "checkpoint.tmp", "checkpoint.state.json")


def write_sidecar(cell: Path, *, step: int, elapsed_seconds: float, saved: Optional[str] = None) -> str:
    saved = saved or stamp_now()
    atomic.write_json(Path(cell) / SIDECAR, {"step": int(step), "elapsed_seconds": round(float(elapsed_seconds), 1),
                                             "saved": saved})
    return saved


def read_sidecar(cell: Path) -> Optional[Dict[str, Any]]:
    p = Path(cell) / SIDECAR
    return read_json(p) if p.is_file() else None


def clear_checkpoint(cell: Path, names: Sequence[str] = DEFAULT_NAMES) -> None:
    for name in names:
        f = Path(cell) / name
        if f.exists():
            f.unlink()


def replace_atomic(tmp: Path, path: Path) -> None:
    """Move a fully written temporary into place."""
    os.replace(tmp, path)


def verify_identity(meta: Dict[str, Any], *, fingerprint: str, unit: str) -> None:
    """Refuse a checkpoint written for another spec or another unit: continuing it would
    report a run nobody registered. ``meta`` may name the unit as ``unit`` or as
    ``variant``/``arm`` plus ``seed``."""
    from rl_researcher.units import parse_unit, unit_id

    arm, seed = parse_unit(unit)
    their_unit = meta.get("unit")
    if their_unit is None and ("variant" in meta or "arm" in meta):
        their_unit = unit_id(str(meta.get("variant", meta.get("arm"))), int(meta.get("seed", -1)))
    if meta.get("spec_fingerprint") != fingerprint or their_unit != unit_id(arm, seed):
        raise SpecError(f"the checkpoint was written for a different spec or unit "
                        f"(fingerprint {meta.get('spec_fingerprint')!r}, unit {their_unit!r}); delete it "
                        f"to start the unit over (the spec changed under a running run)")
