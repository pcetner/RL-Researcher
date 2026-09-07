"""The pre-run questions, asked the same way by ``check`` and by ``run``.

Two callers need them: ``python -m rl_researcher.check``, which reports and exits, and the
runner, which refuses. They live here rather than in either one so that a check cannot be
present in the report and absent from the launch — the failure mode being a run that boots an
engine against a snapshot the check command would have told you was wrong.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from rl_researcher.kinds import Finding, RunKind
from rl_researcher.spec import RunSpec


def staged_checks(stage: str, spec: RunSpec, kind: RunKind, config: Any,
                  out: Optional[Path] = None) -> List[Finding]:
    """The registry's checks for a stage. It is built in a later milestone; until then, none."""
    try:
        import importlib

        checks = importlib.import_module("rl_researcher.checks")
    except ModuleNotFoundError:
        return []
    return list(checks.run_checks(stage, spec, kind, config, out=out))


def collect(kind: RunKind, spec: RunSpec, config: Any = None,
            out: Optional[Path] = None) -> List[Finding]:
    """Everything knowable before anything expensive starts: the kind's own, then the registry's."""
    return list(kind.check(spec, config)) + staged_checks("check", spec, kind, config, out)


def errors(findings: List[Finding]) -> List[Finding]:
    return [f for f in findings if f.level == "error"]
