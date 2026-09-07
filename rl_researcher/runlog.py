"""A log that lives in the repo and is flushed on every line.

A log the human cannot open is the same as no log, and a buffered log is exactly as useful as
no log when the question is whether a run has hung. Every line goes to the file, flushed, and
to the caller's own logger (the terminal by default). A resumed run appends, so the file keeps
what the first attempt recorded.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, TextIO, Tuple

Log = Callable[[str], None]


def open_run_log(path: Optional[Path], user_log: Log = print) -> Tuple[Log, Callable[[], None]]:
    handle: Optional[TextIO] = None
    if path is not None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        handle = Path(path).open("a", encoding="utf-8")

    def log(msg: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        if handle is not None:
            handle.write(f"{stamp} {msg}\n")
            handle.flush()
        user_log(msg)

    def close() -> None:
        if handle is not None:
            handle.close()

    return log, close
