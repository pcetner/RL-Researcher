"""Tell a person something happened, and write it down either way.

The log line is the guaranteed channel and the toast is the courtesy. Windows toast varies by
build, by whether the script host is present, by whether the session is interactive and by
whether focus assist is on; a channel that silently does nothing on some machines must never be
the only record that a run finished at three in the morning.

So :func:`notify` appends to ``<paths.logs>/watcher.log`` first, flushed, and only then tries
the toast. A toast that fails is itself logged, once, at the level of "the toast failed" rather
than as a traceback — a notifier that raises takes the watcher down, and a watcher that is down
is a project nobody is being told about.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

LOG_NAME = "watcher.log"

#: Set once a toast attempt has failed, so a machine that cannot toast says so one time rather
#: than once a minute for the length of a run.
_toast_broken = False


def log_path(config: Any) -> Path:
    d = Path(config.path("logs"))
    d.mkdir(parents=True, exist_ok=True)
    return d / LOG_NAME


def stamp() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def write_line(config: Any, text: str) -> None:
    """Append one line, flushed. A line held in a buffer is a line nobody has been told."""
    with log_path(config).open("a", encoding="utf-8") as fh:
        fh.write(f"{stamp()} {text}\n")
        fh.flush()


def _script() -> Path:
    return Path(__file__).with_name("notify.ps1")


def toast(title: str, body: str, *, timeout: float = 10.0) -> Optional[str]:
    """Try a desktop notification. Returns ``None`` on success, or why it did not happen.

    Never raises. Every failure mode here — no PowerShell, no WinRT, an execution policy, a
    non-interactive session — is a reason to have a quieter machine, not to stop watching.
    """
    global _toast_broken
    if _toast_broken:
        return "toast disabled after an earlier failure"
    if not sys.platform.startswith("win"):
        return f"no toast on {sys.platform}"
    script = _script()
    if not script.is_file():
        return f"no {script.name} beside the package"
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(script), "-Title", title, "-Body", body],
            capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        _toast_broken = True
        return f"{type(exc).__name__}: {exc}"
    if done.returncode != 0:
        _toast_broken = True
        return (done.stderr or done.stdout or f"exit {done.returncode}").strip().splitlines()[0][:200]
    return None


def notify(config: Any, title: str, body: str, *, quiet: bool = False,
           echo: bool = True) -> List[str]:
    """The log line always; the toast unless ``quiet``. Returns what was written."""
    written = [f"{title} — {body}" if body else title]
    write_line(config, written[0])
    if echo:
        print(f"  {written[0]}", flush=True)
    if not quiet:
        why = toast(title, body)
        if why:
            written.append(f"(no toast: {why})")
            write_line(config, written[-1])
    return written
