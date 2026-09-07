"""Hot-stop: ask a unit to checkpoint and stop, without losing anything.

POSIX: SIGTERM sets a flag the unit's loop checks between steps. Windows delivers no SIGTERM
to a console process (a shutdown kills it outright), so there the cadence checkpoint is the
guarantee and Ctrl-C raises :class:`KeyboardInterrupt`, which a unit turns into
:class:`HotStop` after saving.
"""

from __future__ import annotations

import platform
import signal
import threading

_requested = False


class HotStop(KeyboardInterrupt):
    """Raised after a unit has been checkpointed on SIGTERM / Ctrl-C; re-run the same command
    to continue."""


def request_stop() -> None:
    global _requested
    _requested = True


def stop_requested() -> bool:
    return _requested


def install_stop_handler() -> None:
    """Reset the flag and, where the platform allows, route SIGTERM to it."""
    global _requested
    _requested = False
    if platform.system() == "Windows" or threading.current_thread() is not threading.main_thread():
        return
    try:
        signal.signal(signal.SIGTERM, lambda *_: request_stop())
    except (ValueError, OSError):  # pragma: no cover - not the main thread / no signals
        pass
