"""The pre-run questions, asked the same way by ``check`` and by ``run``.

Two callers need them: ``python -m rl_researcher.check``, which reports and exits, and the
runner, which refuses. They live here rather than in either one so that a check cannot be
present in the report and absent from the launch — the failure mode being a run that boots an
engine against a snapshot the check command would have told you was wrong.

The same reasoning fixes which *stages* are asked: see :data:`STAGES`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from rl_researcher.kinds import Finding, RunKind
from rl_researcher.spec import RunSpec


#: The stages both callers ask. ``check`` is everything knowable from the spec before anything
#: expensive; ``run`` is the pair whose question is about *this machine, now* — that the canary
#: still passes on the code about to run, and that nothing under the output directory is staged
#: for commit. Asking only ``check`` meant the second pair were asked by `lint` and by the git
#: hook and never at a launch, which is the one moment they are named for.
STAGES = ("check", "run")


def staged_checks(stage: str, spec: RunSpec, kind: RunKind, config: Any,
                  out: Optional[Path] = None, root: Optional[Path] = None) -> List[Finding]:
    """The registry's checks for one stage."""
    from rl_researcher import checks

    return list(checks.run_checks(stage, spec, kind, config, out=out, root=root))


def collect(kind: RunKind, spec: RunSpec, config: Any = None,
            out: Optional[Path] = None) -> List[Finding]:
    """Everything knowable before anything expensive starts: the kind's own, then the registry's.

    ``out`` and the project root are passed through because the ``run``-stage checks need them:
    one reads the run's directory, the other asks git about the project.
    """
    root = getattr(config, "root", None) if config is not None else None
    found = list(kind.check(spec, config))
    for stage in STAGES:
        found += staged_checks(stage, spec, kind, config, out, root)
    return found


def errors(findings: List[Finding]) -> List[Finding]:
    return [f for f in findings if f.level == "error"]
