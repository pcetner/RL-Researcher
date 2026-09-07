"""The toy run kind: the framework's own canary.

Two arms fit a line to noisy points by gradient descent, three seeds each, in about a second
per unit. It exercises everything a real kind does: a spec with bars, a heartbeat, a JSON
checkpoint and a resume, a hot-stop, registered metrics, a figure, a summary. The framework's
end-to-end test runs it from ``check`` to ``state``; a project can run it on a new machine to
see the whole pipeline work before trusting it with a night of compute.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from rl_researcher.checkpoint import clear_checkpoint, verify_identity
from rl_researcher.kinds import BaseKind, CurveSpec, DeviceInfo, RunContext, UnitContext, UnitResult, units_from
from rl_researcher.spec import MetricRegistry, RunSpec, SpecError
from rl_researcher.stop import HotStop
from rl_researcher.units import read_json

REGISTRY = MetricRegistry(
    known={
        "slope_error": "Absolute error of the fitted slope against the true slope of 2.0. Zero is a perfect fit.",
        "r2": "Fraction of the variance in the points that the fitted line explains. One is a perfect fit; "
              "zero is no better than the mean.",
        "loss": "Final mean squared error on the training points.",
        "seconds": "Wall time the unit took.",
    },
    formulas={"slope_error": r"|\hat{m} - m|", "r2": r"1 - \dfrac{\sum (y - \hat{y})^2}{\sum (y - \bar{y})^2}",
              "loss": r"\dfrac{1}{n}\sum (y - \hat{y})^2"},
    titles={"slope_error": "Slope error", "r2": "R squared", "loss": "Training loss", "seconds": "Seconds"},
)

TRUE_SLOPE, TRUE_INTERCEPT, N_POINTS = 2.0, -1.0, 200
CHECKPOINT_NAME = "checkpoint.state.json"


class ToyKind(BaseKind):
    name = "toy"
    registry = REGISTRY

    # ── the spec ──────────────────────────────────────────────────────────────────────────
    def load(self, path: Path) -> RunSpec:
        spec = super().load(path)
        arms = spec.extra.get("arms")
        if not arms:
            raise SpecError("a toy spec needs [[arms]] tables with a name and a noise")
        for a in arms:
            if "name" not in a:
                raise SpecError("every toy arm needs a name")
        return spec

    def units(self, spec: RunSpec) -> List[str]:
        return units_from([a["name"] for a in spec.extra["arms"]], spec.seeds)

    def unit_class(self, spec: RunSpec, unit: str) -> str:
        return f"toy/{spec.budget.max_steps}steps"

    def device(self) -> Optional[DeviceInfo]:
        return DeviceInfo(name="cpu", kind="cpu")

    def curves(self, spec: RunSpec) -> List[CurveSpec]:
        return [CurveSpec(key="loss", title="loss")]

    def _arm(self, spec: RunSpec, name: str) -> Dict[str, Any]:
        return next(a for a in spec.extra["arms"] if a["name"] == name)

    # ── one unit ──────────────────────────────────────────────────────────────────────────
    def run_unit(self, spec: RunSpec, unit: str, prepared: Any, ctx: UnitContext) -> UnitResult:
        arm = self._arm(spec, ctx.arm)
        noise = float(arm.get("noise", 0.1))
        lr = float(spec.extra.get("learning_rate", 0.05))
        stop_at = spec.extra.get("stop_at_step")           # tests: hot-stop here, once
        slow = float(spec.extra.get("seconds_per_step", 0.0))  # tests: make a unit take time
        rng = np.random.default_rng(ctx.seed)
        x = rng.uniform(-1, 1, N_POINTS)
        y = TRUE_SLOPE * x + TRUE_INTERCEPT + rng.normal(0, noise, N_POINTS)

        m, b = 0.0, 0.0
        step, elapsed_before, resumed_from = 0, 0.0, None
        history: Dict[str, List[float]] = {"loss": []}
        ck_path = ctx.cell / CHECKPOINT_NAME
        ck = read_json(ck_path) if (ctx.resume and ck_path.is_file()) else None
        if ck is not None:
            verify_identity(ck, fingerprint=ctx.fingerprint, unit=unit)
            m, b, step = float(ck["m"]), float(ck["b"]), int(ck["step"])
            elapsed_before = float(ck["elapsed_seconds"])
            history = {k: [float(v) for v in vs] for k, vs in ck["history"].items()}
            resumed_from = step
            ctx.log(f"[{ctx.arm} seed {ctx.seed}] resumed at step {step}")
        elif not ctx.resume:
            clear_checkpoint(ctx.cell)

        t0 = time.time()
        last_beat = t0
        last_ck = t0

        def elapsed() -> float:
            return elapsed_before + (time.time() - t0)

        def checkpoint() -> None:
            payload = {"spec_fingerprint": ctx.fingerprint, "unit": unit, "m": m, "b": b, "step": step,
                       "elapsed_seconds": elapsed(), "history": history}
            from rl_researcher import atomic

            atomic.write_json(ck_path, payload)
            ctx.sidecar(step=step, elapsed_seconds=elapsed())

        def beat(status: str) -> None:
            done = step - (resumed_from or 0)
            rate = done / (time.time() - t0) if done > 0 and time.time() > t0 else None
            eta = (ctx.max_steps - step) / rate if rate else None
            ctx.beat(status, step=step, elapsed_seconds=elapsed(), rate=rate, eta_seconds=eta,
                     last={"loss": history["loss"][-1] if history["loss"] else float("nan")}, history=history)

        status = "complete"
        beat("resumed" if ck is not None else "running")
        while step < ctx.max_steps:
            if elapsed() > ctx.max_seconds:
                status = "incomplete"
                break
            pred = m * x + b
            err = pred - y
            loss = float(np.mean(err ** 2))
            m -= lr * float(np.mean(2 * err * x))
            b -= lr * float(np.mean(2 * err))
            step += 1
            history["loss"].append(loss)
            if slow:
                time.sleep(slow)
            now = time.time()
            if now - last_beat >= min(ctx.cadence.heartbeat_seconds, 0.5):
                beat("running")
                last_beat = now
            if step % ctx.cadence.checkpoint_every_steps == 0 or now - last_ck >= ctx.cadence.checkpoint_seconds:
                checkpoint()
                last_ck = now
            if ctx.stop_requested() or (stop_at is not None and step == int(stop_at) and resumed_from is None):
                checkpoint()
                beat("stopped")
                raise HotStop(f"stopped at step {step}; re-run the same command to continue")
        beat("evaluating")
        pred = m * x + b
        ss_res = float(np.sum((y - pred) ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        metrics = {"slope_error": abs(m - TRUE_SLOPE), "r2": 1 - ss_res / ss_tot if ss_tot else float("nan"),
                   "loss": history["loss"][-1] if history["loss"] else float("nan"), "seconds": elapsed()}
        return UnitResult(arm=ctx.arm, seed=ctx.seed, status=status, steps=step, max_steps=ctx.max_steps,
                          seconds=elapsed(), metrics=metrics, history=history, params=2,
                          resumed_from_step=resumed_from, extra={"m": m, "b": b, "noise": noise})

    # ── the run ───────────────────────────────────────────────────────────────────────────
    def summarise(self, spec: RunSpec, results: List[Dict[str, Any]], out: Path, ctx: RunContext) -> Dict[str, Any]:
        figures: Dict[str, str] = {}
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            from rl_researcher import plotstyle as ps

            fig_dir = out / "figures"
            fig_dir.mkdir(parents=True, exist_ok=True)
            with ps.style():
                fig, ax = plt.subplots(figsize=(6, 3.2))
                arms = [a["name"] for a in spec.extra["arms"]]
                for r in results:
                    if not r.get("history", {}).get("loss"):
                        continue
                    ax.plot(r["history"]["loss"], color=ps.variant_color(r["arm"], arms), alpha=0.8,
                            label=f"{r['arm']} seed {r['seed']}")
                ax.set_xlabel("step")
                ax.set_ylabel("loss")
                ax.set_yscale("log")
                ax.legend(fontsize=7, frameon=False)
                path = fig_dir / "loss.png"
                ps.savefig(fig, path)
                plt.close(fig)
            figures["loss"] = path.relative_to(out).as_posix()
        except Exception as exc:  # noqa: BLE001 - a figure must never fail the canary's summary
            ctx.log(f"toy figure skipped: {type(exc).__name__}: {exc}")
        return {"figures": figures, "true_slope": TRUE_SLOPE}

    def estimator_name(self, metric: str) -> str:
        return "rl_researcher.examples.toy.kind.ToyKind.run_unit"


def write_example_spec(path: Path, *, max_steps: int = 300, seeds=(0, 1, 2), extra: str = "") -> Path:
    """The toy spec, for tests and for a first run on a new machine."""
    seeds_s = ", ".join(str(s) for s in seeds)
    text = f'''# The framework's canary: two arms fit a line to noisy points.
name = "toy-line-fit"
kind = "toy"
seeds = [{seeds_s}]
hypothesis = """
A line fitted by gradient descent recovers the true slope on quiet data and not on noisy data.
Prediction: the ols arm clears both bars and the noisy arm misses slope_error. The result that
would change the plan most is the noisy arm clearing slope_error, which would mean the bar is
too loose to discriminate anything.
"""
decision_touches = ["the framework canary"]
conjunction = ["slope_error", "r2"]
{extra}
[[arms]]
name = "ols"
noise = 0.1

[[arms]]
name = "noisy"
noise = 1.0

[budget]
max_steps = {max_steps}
max_seconds = 60

[cadence]
heartbeat_seconds = 5
checkpoint_every_steps = 50
checkpoint_seconds = 30

[[metrics]]
name = "slope_error"
baseline = "true slope 2.0"
direction = "lower"
bar = 0.1
why = "The number the fit is for."

[[metrics]]
name = "r2"
baseline = "the mean of y"
direction = "higher"
bar = 0.9
why = "Whether the line explains the points at all."

[[metrics]]
name = "r2"
'''
    # the duplicate name above is removed here so the spec stays valid; kept explicit so a
    # test can assert the loader refuses duplicates by writing its own
    text = text[: text.rindex('[[metrics]]\nname = "r2"\n')]
    text += '''[[metrics]]
name = "loss"
direction = "report"
why = "Continuity with the curve on the dashboard."
'''
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _dump(d: Dict[str, Any]) -> str:  # pragma: no cover - debugging aid
    return json.dumps(d, indent=2, default=str)
