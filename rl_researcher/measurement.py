"""The base for a run kind that measures rather than trains.

A measurement answers one question about data that already exists. It has no arms, no seeds and
no bars, because there is nothing to compare and nothing was predicted: it is usually run after
a null, to work out which family of fix the null implicates. It trains nothing, so it is cheap,
repeatable, and safe to re-run whenever the code that reads the data changes.

It still goes through the runner, and that is the point. A measurement gets the lock, the
flushed log, the heartbeat, the guards, the pre-run checks, the cost gate, the hot stop and the
provenance stamp for free, and it cannot quietly grow its own versions of them — which is
exactly what the four scripts this replaces each did.

Subclass it and implement :meth:`measure`. Everything else — what the document asks, what its
tables are, what it concluded — is declared, and the framework lays it out:

    class RewardDiagnostics(MeasurementKind):
        name = "reward-diagnostics"
        def question(self, spec, payload): ...
        def method(self, spec, payload):  ...   # (label, value) pairs
        def tables(self, spec, payload):  ...   # (title, markdown) pairs
        def verdict(self, spec, payload): ...
        def measure(self, spec, prepared, ctx) -> dict: ...
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from rl_researcher.kinds import BaseKind, RunContext, UnitContext, UnitResult
from rl_researcher.spec import RunSpec

#: The one unit every measurement has. It is spelled as an arm and a seed because that is the
#: layout every other artefact on disk uses, and a measurement with its own directory shape
#: would be a second thing for `status`, the page and the watcher to know about.
UNIT = "measure/seed0"


class MeasurementKind(BaseKind):
    """A run kind whose unit is one pass over data that already exists."""

    name = "measurement"
    artefact_kind = "measurement"
    log_name = "measure.log"
    unit_noun = "pass"
    step_noun = "step"

    #: A measurement is deterministic given its data: a second seed produces the same number,
    #: so there is no spread to read and C05 does not ask for one.
    compares_seeds = False

    #: The page's four unit tables are about arms and seeds; a measurement has one of each.
    page_sections = ("metrics", "running", "failed", "log")

    def units(self, spec: RunSpec) -> List[str]:
        return [UNIT]

    def unit_class(self, spec: RunSpec, unit: str) -> str:
        return f"{self.name}/measurement"

    # ------------------------------------------------------------------ the measurement

    def measure(self, spec: RunSpec, prepared: Any, ctx: UnitContext) -> Dict[str, Any]:
        """Compute the numbers and return them as JSON-serialisable data.

        Everything the document says must be derivable from what this returns. A measurement
        whose writer reaches back into the data is a measurement whose document cannot be
        regenerated from disk, which is the property that makes it checkable.
        """
        raise NotImplementedError

    def metrics_of(self, payload: Dict[str, Any]) -> Dict[str, float]:
        """The scalars worth carrying into the ledger and onto the page. Default: none, because
        most of a measurement's output is a table rather than a headline number."""
        return {}

    def run_unit(self, spec: RunSpec, unit: str, prepared: Any, ctx: UnitContext) -> UnitResult:
        started = time.time()
        ctx.beat("running", step=0, elapsed_seconds=0.0)
        payload = dict(self.measure(spec, prepared, ctx) or {})
        payload.setdefault("measured", time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()))
        payload.setdefault("seconds", round(time.time() - started, 1))
        return UnitResult(arm="measure", seed=0, status="complete", steps=1, max_steps=1,
                          seconds=round(time.time() - started, 1),
                          metrics=self.metrics_of(payload), extras={"payload": payload})

    # ------------------------------------------------------------------ what it leaves behind

    def payload_of(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        """The measurement's own data, from the run summary — so the document can be rebuilt
        from disk without re-measuring anything."""
        got = summary.get("payload")
        if isinstance(got, dict):
            return got
        for r in summary.get("runs") or []:
            inner = (r.get("extras") or {}).get("payload")
            if isinstance(inner, dict):
                return inner
        return {}

    def summarise(self, spec: RunSpec, results: List[Dict[str, Any]], out: Path,
                  ctx: RunContext) -> Dict[str, Any]:
        from rl_researcher.artefacts.measurement import write_measurement

        payload = self.payload_of({"runs": results}) or self.payload_of(ctx.previous or {})
        # From the context, never from git: on a rebuild the run's own provenance stands, and a
        # regenerated page must not attribute the numbers to whatever re-rendered them.
        payload.setdefault("git_sha", ctx.commit)
        payload.setdefault("rl_researcher", ctx.framework)
        path = write_measurement(spec, payload, out, kind=self, ledger=_ledger(ctx),
                                 command=f"python -m rl_researcher.report {spec.name}")
        ctx.log(f"measurement -> {path}")
        return {"payload": payload, "figures": payload.get("figures") or {}}


def _ledger(ctx: RunContext) -> Optional[Any]:
    """The project's ledger, when there is a project. A measurement driven straight from the
    library has no config and therefore no ledger, and that must cost it the rows rather than
    the document."""
    from rl_researcher.ledger import open_ledger

    if getattr(ctx, "config", None) is None:
        return None
    try:
        return open_ledger(ctx.config)
    except (OSError, ValueError):
        return None
