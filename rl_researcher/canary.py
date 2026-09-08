"""The canary result: which commit the machinery was last shown to work at.

The canary is the project's own five-minute end-to-end exercise of the pipeline, named in
``rl-researcher.toml`` under ``[canary] spec``. What makes it worth anything is a record of
*when* it last passed, because a canary that predated the change it was meant to catch says
nothing about that change (L009). C09 reads that record before a gated run; the state page's
Health section reads it so a person can see it without running a check.

This module is the record: one path, one reader, one writer, in the shape ``cost.py`` already
uses for the throughput table. The two were always the same idea — running the canary is what
calibrates the cost estimate *and* what says the machinery still works — and until this existed
only the first half was written down.

Three conditions have to hold before anything is recorded, and each is a way the record could
otherwise lie:

* the run has to be the canary, decided by :func:`is_canary` rather than by a name comparison;
* the canary has to have passed — every unit complete, none missing;
* git has to be able to name the commit. A result stamped ``unknown`` reads as fresh forever,
  because the staleness comparison cannot resolve it and a comparison that cannot run is not a
  comparison that passed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rl_researcher import atomic
from rl_researcher.config import Config, resolve_spec
from rl_researcher.spec import RunSpec
from rl_researcher.units import stamp_now

CANARY_NAME = "canary.json"

#: A commit git could not name. The runner writes this into a summary rather than failing a run,
#: which is right there and wrong here: it must never reach the record C09 reads.
UNKNOWN = "unknown"


def canary_path(config: Config) -> Path:
    return config.path("ledger") / CANARY_NAME


def read_canary(config: Config) -> Dict[str, Any]:
    """The canary result, or ``{}``. The one reader, so the check and the state page cannot
    disagree about what an unreadable file means."""
    p = canary_path(config)
    if not p.is_file():
        return {}
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return blob if isinstance(blob, dict) else {}


def is_canary(spec: Optional[RunSpec], config: Optional[Config]) -> bool:
    """Whether this spec is the project's canary.

    ``[canary] spec`` is documented as a path (``studies/canary.toml``) and a spec's ``name`` is
    a bare word, so comparing the two never matched and the canary warned about itself in every
    project that had one. Both spellings are resolved here, through the same
    :func:`~rl_researcher.config.resolve_spec` every command uses to accept a spec argument.
    """
    if spec is None or config is None:
        return False
    written = getattr(getattr(config, "canary", None), "spec", None)
    if not written:
        return False
    source = getattr(spec, "source_path", None)
    if source:
        try:
            if Path(resolve_spec(written, config)).resolve() == Path(source).resolve():
                return True
        except (OSError, ValueError, FileNotFoundError):
            pass
    # The spec file may be gone, or named from somewhere else on disk; the name still decides.
    return Path(str(written)).stem == getattr(spec, "name", None)


def _why_not(summary: Dict[str, Any]) -> str:
    """Why this finished run is not evidence the machinery works. Empty means it is."""
    missing = list(summary.get("missing_units") or [])
    if missing:
        return f"{len(missing)} unit(s) never ran ({', '.join(map(str, missing[:3]))})"
    runs = list(summary.get("runs") or [])
    if not runs:
        return "the run has no units"
    bad = [str(r.get("unit") or "?") for r in runs if str(r.get("status")) != "complete"]
    if bad:
        return f"{len(bad)} unit(s) did not complete ({', '.join(bad[:3])})"
    commit = str(summary.get("git_sha") or "")
    if not commit or commit == UNKNOWN:
        return ("git cannot name the commit this ran at, and a result recorded without one "
                "reads as fresh forever")
    return ""


def record_canary(config: Optional[Config], spec: Optional[RunSpec],
                  summary: Dict[str, Any]) -> Tuple[Optional[Path], str]:
    """Record that the canary passed, if it did. Returns ``(path, why_not)``.

    Both empty means this run is not the canary and there is nothing to say about it. A ``why``
    with no path is a canary run that is not evidence, and the caller says so out loud: a person
    who ran the canary and got nothing recorded is owed the reason, or they will run it again.
    """
    if not is_canary(spec, config) or config is None:
        return None, ""
    why = _why_not(summary)
    if why:
        return None, why
    path = canary_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_json(path, {
        "commit": str(summary.get("git_sha")),
        "date": stamp_now(),
        "run": str(summary.get("run") or getattr(spec, "name", "")),
        "rl_researcher": str(summary.get("rl_researcher") or ""),
        "device": summary.get("device"),
        "wall_seconds": summary.get("wall_seconds"),
        "units": len(list(summary.get("runs") or [])),
    })
    return path, ""


def changed_since(root: Path, commit: str, watched: List[str]) -> Optional[List[str]]:
    """Which watched paths differ between ``commit`` and the working tree.

    ``None`` means git could not answer — an unresolvable commit, or no repository — which is
    not the same answer as ``[]`` and must not be read as one. Reading them as one is how a
    canary result naming a commit nobody has silences C09 for good.
    """
    import subprocess

    if not watched:
        return []
    try:
        done = subprocess.run(["git", "diff", "--name-only", commit, "--", *watched],
                              cwd=str(root), capture_output=True, text=True, timeout=15,
                              check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return sorted({line.split("/")[-1] for line in done.stdout.splitlines() if line.strip()})
