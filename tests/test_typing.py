"""A kind that declares a spec of its own type-checks.

`docs/run-kinds.md` tells a kind with structure of its own to declare a `RunSpec` subclass with
real fields, so that a misspelled knob is caught at load time rather than as a `KeyError` an
hour into a run. Every kind that took that advice then narrowed the parameter its base declares
— `units(self, spec: StudySpec)` against `units(self, spec: RunSpec)` — which is a Liskov
violation, and which a type checker reports as one on every such method.

That was invisible here for two reasons. This package's own kinds do not subclass `RunSpec`, so
its `mypy` run never saw it; and in the consuming project it was hidden by an editable install
that `mypy` could not resolve, under `ignore_missing_imports`, which turns the whole framework
into `Any`. It showed up only in that project's CI, where the framework is a real wheel.

So the property is checked here, from the outside, the way a consumer sees it.
"""

import importlib.util
import subprocess
import sys
import textwrap

import pytest

pytestmark = [pytest.mark.tier1]

#: A kind of the shape the documentation recommends: its own spec, and every method that takes
#: one narrowed to it.
CONSUMER = '''
from pathlib import Path
from typing import Any, Dict, List

from rl_researcher.kinds import BaseKind, RunContext, UnitContext, UnitResult
from rl_researcher.spec import MetricRegistry, RunSpec


class StudySpec(RunSpec):
    snapshot: str = ""


class StudyKind(BaseKind[StudySpec]):
    name = "study"
    registry = MetricRegistry(known={"r2": "fit"})

    def load(self, path: Path) -> StudySpec:
        raise NotImplementedError

    def units(self, spec: StudySpec) -> List[str]:
        return [spec.snapshot]

    def unit_class(self, spec: StudySpec, unit: str) -> str:
        return spec.snapshot

    def run_unit(self, spec: StudySpec, unit: str, prepared: Any,
                 ctx: UnitContext) -> UnitResult:
        raise NotImplementedError

    def summarise(self, spec: StudySpec, results: List[Dict[str, Any]], out: Path,
                  ctx: RunContext) -> Dict[str, Any]:
        return {"snapshot": spec.snapshot}


def takes_any_kind() -> None:
    """The framework does not care which spec a kind reads, and says so as `RunKind[Any]`."""
    from rl_researcher.kinds import RunKind

    kind: "RunKind[Any]" = StudyKind()
    assert kind is not None
'''


@pytest.mark.skipif(importlib.util.find_spec("mypy") is None,
                    reason="mypy is a dev dependency; there is nothing to ask without it")
def test_a_kind_may_declare_its_own_spec_without_violating_its_base(tmp_path):
    src = tmp_path / "consumer.py"
    src.write_text(textwrap.dedent(CONSUMER), encoding="utf-8")

    done = subprocess.run([sys.executable, "-m", "mypy", "--no-incremental", str(src)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "override" not in done.stdout, done.stdout
