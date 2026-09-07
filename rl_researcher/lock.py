"""One run per output directory.

Two runs sharing an output directory do not conflict loudly: they skip each other's finished
units, resume each other's checkpoints, and write the same summary twice. The numbers can even
agree, which is worse, because nothing says the run happened twice. A live lock is therefore a
refusal; a lock whose process is gone (a kill, a shutdown) is stale and gets taken over, which
is exactly the hot-start case.
"""

from __future__ import annotations

import json
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


def _claim(path: Path, run: str) -> bool:
    """Create the lock file, or say it was already there. The create is the claim.

    ``O_CREAT | O_EXCL`` is what makes two starts a second apart resolve rather than both
    succeed: reading first and writing after leaves a window in which neither sees the other,
    and the loser of that race is a directory with two runs writing into it, which is the one
    failure this file exists to prevent.
    """
    payload = json.dumps({"pid": os.getpid(), "host": platform.node(), "run": run,
                          "started": stamp_now()}, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(payload)
    return True


def acquire_lock(out: Path, run: str, log: Callable[[str], None]) -> Path:
    """Claim ``out`` for this process, or raise :class:`RunLocked`."""
    path = Path(out) / LOCK_NAME
    if _claim(path, run):
        return path
    held = read_json(path)
    if held is not None:
        pid, host = held.get("pid"), held.get("host")
        if pid is not None and host == platform.node() and process_alive(int(pid)):
            raise RunLocked(
                f"run {run} is already running in {out} as pid {pid} (started "
                f"{held.get('started', '?')}). Two runs on one output directory overwrite each "
                f"other's units. Wait for it, or stop it and re-run to continue from its "
                f"checkpoints. If you are sure it is gone, delete {path}.")
        if host is not None and host != platform.node():
            # Nothing here can ask another machine whether its process is alive, so this is a
            # guess, and it is the dangerous direction of one: `colab_mirror` exists precisely
            # to put a run's directory on a shared path. Said loudly rather than in passing.
            log(f"** taking over a lock held by {host} (pid {pid}, started "
                f"{held.get('started', '?')}). Liveness cannot be checked across machines. If "
                f"that run is still going, both will write into {out} **")
        else:
            log(f"taking over a stale lock from pid {pid} ({held.get('started', '?')}); "
                f"that process is no longer running")
    else:
        log(f"the lock at {path} could not be read; taking it over")
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
