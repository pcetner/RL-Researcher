"""Atomic file writes that survive a Windows scanner (PLAN §6: fail loud, but not on noise).

Every file the study runner writes while training goes through here: the per-cell heartbeat,
``results.json``, and the live dashboard. All three use the same temp-file-then-replace pattern,
because a reader can otherwise catch a half-written file — a browser reloading the dashboard
every fifteen seconds, or ``study.py status`` reading a heartbeat mid-write.

**Why the retry.** On Windows ``os.replace`` fails with ``PermissionError`` (WinError 5) when
another process holds either file open for the instant it takes to read it — Defender's realtime
scanner, the search indexer, a backup agent. It is transient, it has nothing to do with the
caller, and it is most likely on the *first* write into a freshly created directory, which is
exactly when a scanner notices new files. Study 5 lost eleven queued cells to one such failure
five hours into a run, because the runner aborts the whole study on any exception — the right
policy for a real error and the wrong outcome for a lock that would have cleared in 150 ms.

Retrying briefly is the entire fix. A genuine permission problem still raises, just later.
"""

from __future__ import annotations

import time
from pathlib import Path

ATTEMPTS = 8
DELAY = 0.15  # seconds; doubles each attempt, so ~19 s of patience in the worst case


def replace_with_retry(tmp: Path, target: Path, *, attempts: int = ATTEMPTS,
                       delay: float = DELAY) -> None:
    """``tmp.replace(target)``, retried through a transient lock on either path.

    ``time.sleep`` is called through the module rather than captured as a default argument, so
    a test can patch ``atomic.time.sleep`` and not spend the real backoff.
    """
    wait = delay
    for attempt in range(attempts):
        try:
            tmp.replace(target)
            return
        except PermissionError:
            # Windows only, and only ever transient. The last attempt re-raises so a real
            # permission fault still reaches the operator rather than being swallowed.
            if attempt == attempts - 1:
                raise
            time.sleep(wait)
            wait *= 2


def temp_for(target: Path) -> Path:
    """The temporary ``target`` is written through, beside it so the replace stays atomic.

    The whole name plus a suffix, not ``with_suffix``, which replaces the extension: that gave
    ``report.md`` and ``report.html`` one temporary between them, and on a case-insensitive
    filesystem ``STATE.md`` and ``state.json`` another. Nothing writes two of those at once
    today; this is so nothing can start to.
    """
    return target.with_name(target.name + ".tmp")


def write_text(target: Path, text: str, *, encoding: str = "utf-8") -> Path:
    """Write ``text`` to ``target`` atomically: no reader ever sees a partial file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = temp_for(target)
    tmp.write_text(text, encoding=encoding)
    replace_with_retry(tmp, target)
    return target


def write_json(target: Path, payload, *, indent: int = 2) -> Path:
    """``write_text`` for a JSON document; non-JSON values are stringified rather than fatal."""
    import json

    return write_text(target, json.dumps(payload, indent=indent, default=str))
