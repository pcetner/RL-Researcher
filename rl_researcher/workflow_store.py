"""Cross-process workflow serialization and recoverable request receipts.

Lock order is workflow -> ledger. OS locks are released when a process exits;
a timeout never removes another writer's lock file.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from rl_researcher import atomic


class WorkflowError(ValueError):
    def __init__(self, code: str, text: str, status: int = 409, **details: Any):
        super().__init__(text)
        self.status = status
        self.data = {"error": text, "reason_code": code, **details}


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()


def read(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, type(default)):
            raise ValueError("unexpected document shape")
        return value
    except (OSError, ValueError) as exc:
        raise WorkflowError(
            "workflow_store_invalid",
            f"Cannot read {path}: {exc}. Open and repair this workflow file.",
        ) from exc


_mutex = threading.RLock()
_local = threading.local()


@contextmanager
def exclusive(config: Any, timeout: float = 5) -> Iterator[None]:
    path = config.path("ledger") / "workflow.lock"
    key = str(path.resolve())
    if not _mutex.acquire(timeout=timeout):
        raise WorkflowError("workflow_busy", "Another workflow is saving. Retry shortly.")
    try:
        held: set[str] = getattr(_local, "held", set())
        if key in held:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as stream:
            stream.seek(0, 2)
            if not stream.tell():
                stream.write(b"0")
                stream.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise WorkflowError(
                            "workflow_busy", "Another workflow is saving. Retry shortly."
                        )
                    time.sleep(0.025)
            _local.held = held | {key}
            try:
                yield
            finally:
                _local.held = held
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]

    finally:
        _mutex.release()


def actor(config: Any) -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "config", "user.name"],
                cwd=config.root,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            ).strip()
            or "Local user"
        )
    except (OSError, subprocess.SubprocessError):
        return "Local user"


def receipts(config: Any) -> dict:
    return read(config.path("ledger") / "operations.json", {})


def begin(config: Any, action: str, payload: dict) -> tuple[str, str, Any]:
    operation = payload.get("operation_id")
    if not isinstance(operation, str) or not operation.strip() or len(operation) > 200:
        raise WorkflowError("invalid_operation", "A nonempty operation_id is required.", 400)
    binding = digest({"action": action, "payload": payload})
    journal = receipts(config)
    previous = journal.get(operation)
    if previous and previous["binding"] != binding:
        raise WorkflowError(
            "operation_id_reused", "This operation ID belongs to a different request."
        )
    if previous and "receipt" in previous:
        return operation, binding, previous["receipt"]
    # A target write can succeed before its journal receipt. The caller recovers it
    # from the authoritative event before checking optimistic revisions.
    journal[operation] = {"action": action, "run": payload.get("run"), "binding": binding}
    atomic.write_json(config.path("ledger") / "operations.json", journal)
    return operation, binding, None


def finish(config: Any, operation: str, binding: str, receipt: dict) -> dict:
    journal = receipts(config)
    if journal[operation]["binding"] != binding:
        raise WorkflowError("operation_id_reused", "Operation binding changed.")
    journal[operation]["receipt"] = receipt
    atomic.write_json(config.path("ledger") / "operations.json", journal)
    return receipt


def launch_path(config: Any) -> Path:
    return config.path("ledger") / "launches.json"


def pending(config: Any, run: str) -> bool:
    from rl_researcher.lock import process_alive

    reservation = read(launch_path(config), {}).get(run)
    if not reservation:
        return False
    pid = reservation.get("pid")
    return process_alive(pid if pid is not None else reservation.get("parent_pid", 0))


def clear_launch(config: Any, run: str) -> None:
    reservations = read(launch_path(config), {})
    if run in reservations:
        del reservations[run]
        atomic.write_json(launch_path(config), reservations)
