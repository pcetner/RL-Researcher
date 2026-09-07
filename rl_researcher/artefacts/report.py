"""Turning a run's per-unit numbers into the ones a report states.

Four small functions, and the reason each is worth having in one place is a mistake that has
already been paid for:

* :func:`mean_std` keeps "not computed", "measured and blew up" and "measured" apart. Study 3
  collapsed them and reported a variant as failing a bar that two of its three seeds cleared.
* :func:`fmt` says ``n/a`` when nothing was measured and names the diverged seeds when some
  were, so a reader is never shown a mean without being told what it is a mean of.
* :func:`passes` is the single rule for whether a value clears a bar. Two rules existed before
  this: the report was strict and the loop dashboard was inclusive, so the same number could
  pass on one page and fail on another. One rule now, and it is the strict one: a bar is a
  level to beat, and matching the control arm exactly is not evidence of anything.
* :func:`aggregate` groups units by arm, whatever the kind calls that field.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

Stat = Tuple[float, float, int, int]  # (mean, std, n, diverged)


def mean_std(values: Iterable[Any]) -> Stat:
    """``(mean, std, n, diverged)`` over the seeds that produced a number.

    Three outcomes are distinguished:

    * **NaN** - not computed (a metric that does not apply to this arm). Excluded entirely;
      ``n`` counts what was measured, and the cell renders ``n/a``.
    * **infinite** - measured, and the measurement blew up. Counted in ``diverged``, kept out
      of the mean so one runaway rollout cannot swallow the seeds that worked, and reported.
    * finite - averaged.

    The spread is the population standard deviation, and it is 0.0 for a single seed rather
    than undefined, because a report prints it either way.
    """
    real = [float(v) for v in values
            if v is not None and not (isinstance(v, float) and math.isnan(v))]
    vals = [v for v in real if math.isfinite(v)]
    diverged = len(real) - len(vals)
    if not vals:
        return float("nan"), float("nan"), 0, diverged
    m = sum(vals) / len(vals)
    s = (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5 if len(vals) > 1 else 0.0
    return m, s, len(vals), diverged


def fmt(m: float, s: float, n: int, diverged: int = 0) -> str:
    """One cell of a metrics table: the mean, its spread, and what was thrown away."""
    if n == 0:
        return f"diverged ({diverged} seeds)" if diverged else "n/a"
    if math.isnan(m):
        return "n/a"
    txt = f"{m:.3f}" if n == 1 else f"{m:.3f} ± {s:.3f}"
    return f"{txt} ({diverged} of {n + diverged} diverged)" if diverged else txt


def passes(direction: str, value: Optional[float], bar: Optional[float], *,
           diverged: int = 0) -> Optional[bool]:
    """Does ``value`` clear ``bar``? ``None`` when there is no bar to clear, or when the
    question cannot be answered from the numbers given.

    A diverged seed is never a pass, whichever way the bar points: without that, ``+inf``
    clears any "higher" bar outright and one blown-up seed clears it for the whole arm.
    """
    if bar is None or value is None or direction not in ("higher", "lower"):
        return None
    try:
        v, b = float(value), float(bar)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isnan(b):
        return None
    if diverged or not math.isfinite(v):
        return False
    if not math.isfinite(b):
        return None
    return v > b if direction == "higher" else v < b


def bar_mark(metric: Any, mean: float, diverged: int = 0, *,
             reference: Optional[float] = None) -> str:
    """``" ✓"``, ``" ✗"`` or ``""`` for one metric of one arm.

    ``metric`` is a ``MetricSpec``. A metric with a ``bar`` is judged against it; a metric with
    a ``compare_to`` is judged against ``reference``, the mean of the arm it names in this same
    run, and reads as unjudged until that arm has one.
    """
    bar = getattr(metric, "bar", None)
    if bar is None and getattr(metric, "compare_to", None):
        bar = reference
    verdict = passes(getattr(metric, "direction", "report"), mean, bar, diverged=diverged)
    return "" if verdict is None else (" ✓" if verdict else " ✗")


def aggregate(runs: Sequence[Dict[str, Any]], metric_names: Sequence[str], *,
              complete_only: bool = False) -> Dict[str, Dict[str, Stat]]:
    """``{arm: {metric: (mean, std, n, diverged)}}`` across the seeds of each arm.

    A unit names its arm as ``arm`` or, in this project's older summaries, as ``variant``.
    ``complete_only`` drops units that hit the time cap before their step budget: where a
    metric grows with steps, averaging a short unit with a full one states a number that is
    about the budget rather than about the arm.
    """
    per: Dict[str, Dict[str, List[float]]] = {}
    for r in runs:
        if complete_only and r.get("status") not in (None, "complete", "done"):
            continue
        arm = str(r.get("arm", r.get("variant", "")))
        got = r.get("metrics") or {}
        v = per.setdefault(arm, {})
        for m in metric_names:
            v.setdefault(m, []).append(float(got.get(m, float("nan"))))
    return {arm: {m: mean_std(vals) for m, vals in ms.items()} for arm, ms in per.items()}
