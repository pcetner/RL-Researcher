"""One run per output directory.

Two runs sharing an output directory do not conflict loudly: they skip each other's finished
units, resume each other's checkpoints, and write the same summary twice. The numbers can even
agree, which is worse, because nothing says the run happened twice. A live lock is therefore a
refusal; a lock whose process is gone (a kill, a shutdown) is stale and gets taken over, which
is exactly the hot-start case.
"""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path
from typing import Callable, Dict, Optional

from rl_researcher import atomic
from rl_researcher.units import read_json, stamp_now

LOCK_NAME = ".study-lock.json"  # the name Auto-SM64's runs already use; kept so nothing renames


class RunLocked(RuntimeError):
    pass


def process_alive(pid: int) -> bool:
    """Whether ``pid`` is running on this machine.

    Not ``os.kill(pid, 0)``: on Windows CPython implements ``os.kill`` with
    ``TerminateProcess``, so the usual liveness probe would kill the very process it is
    asking about.
    """
    try:
        if platform.system() == "Windows":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
                                 capture_output=True, text=True, timeout=10).stdout
            return str(int(pid)) in out
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def acquire_lock(out: Path, run: str, log: Callable[[str], None]) -> Path:
    """Claim ``out`` for this process, or raise :class:`RunLocked`."""
    path = Path(out) / LOCK_NAME
    held = read_json(path) if path.is_file() else None
    if held is not None:
        pid, host = held.get("pid"), held.get("host")
        if pid is not None and host == platform.node() and process_alive(int(pid)):
            raise RunLocked(
                f"run {run} is already running in {out} as pid {pid} (started "
                f"{held.get('started', '?')}). Two runs on one output directory overwrite each "
                f"other's units. Wait for it, or stop it and re-run to continue from its "
                f"checkpoints. If you are sure it is gone, delete {path}.")
        log(f"taking over a stale lock from pid {pid} ({held.get('started', '?')}); "
            f"that process is no longer running")
    atomic.write_json(path, {"pid": os.getpid(), "host": platform.node(), "run": run,
                             "started": stamp_now()})
    return path


def release_lock(path: Optional[Path]) -> None:
    """Drop the lock if this process still owns it."""
    if path is None or not Path(path).is_file():
        return
    held = read_json(Path(path))
    if held is not None and held.get("pid") == os.getpid():
        try:
            Path(path).unlink()
        except OSError:  # pragma: no cover
            pass


def lock_holder(out: Path) -> Optional[Dict]:
    """Who holds this output directory, and whether that process still exists: the lock
    contents plus ``alive`` (``None`` when the lock was written on another machine)."""
    path = Path(out) / LOCK_NAME
    held = read_json(path) if path.is_file() else None
    if held is None:
        return None
    if held.get("host") != platform.node():
        held["alive"] = None
    else:
        held["alive"] = process_alive(int(held.get("pid", -1)))
    return held
