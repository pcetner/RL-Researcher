"""Compact research summaries shared by the board and standalone dashboard."""

from __future__ import annotations

from typing import Any, Dict

from rl_researcher.artefacts.report import aggregate, fmt, judge, tied
from rl_researcher.artefacts.run_report import _arms, _reference, _target, degenerate, outcome
from rl_researcher.blocks.base import md_cell


def research_summary(spec: Any, summary: Dict[str, Any], *, provisional: bool,
                     registry: Any = None) -> Dict[str, Any]:
    """Use the report's estimators and judgments, including unjudgeable comparisons."""
    if not summary.get("runs"):
        return {"headline": "No results yet.", "provisional": True, "rows": [], "markdown": ""}
    arms = _arms(spec, summary)
    headline, _missed = outcome(spec, summary)
    agg = aggregate(summary["runs"], [m.name for m in spec.metrics])
    dead = degenerate(spec, summary, arms)
    warnings = list(dead.values())
    if getattr(spec, "screening", False):
        warnings.insert(0, "Screening run: one seed per arm; use this to choose what to run next.")
    missing = summary.get("missing_units") or summary.get("missing_cells") or []
    if missing:
        warnings.append("Missing results: " + ", ".join(map(str, missing)))
        provisional = True
    rows = []
    for metric in spec.metrics:
        for arm in arms:
            mean, spread, n, div = agg.get(arm, {}).get(
                metric.name, (float("nan"), float("nan"), 0, 0))
            ref = _reference(agg, metric, metric.name)
            verdict = judge(metric, mean, div, reference=ref, arm=arm)
            status = ("Not judged" if metric.name in dead else
                      "Tie" if tied(metric, mean, reference=ref, arm=arm) else
                      "Pass" if verdict is True else "Fail" if verdict is False else "Not judged")
            rows.append({"metric": metric.name, "arm": arm, "target": _target(metric),
                         "title": registry.title(metric.name) if registry else metric.name,
                         "definition": registry.description(metric.name) if registry else "",
                         "formula": registry.formula(metric.name) if registry else "",
                         "result": fmt(mean, spread, n, div), "seeds": n,
                         "diverged": div, "status": status,
                         "criterion": metric.name in (getattr(spec, "conjunction", []) or
                             [m.name for m in spec.metrics if m.bar is not None or m.compare_to]),
                         "why": dead.get(metric.name, "") or metric.why or ""})
    markdown = summary_markdown(headline, rows, provisional)
    if warnings:
        markdown += "\n\n" + "\n\n".join("> " + w for w in warnings)
    return {"headline": headline, "provisional": provisional, "rows": rows,
            "warnings": warnings, "markdown": markdown}


def summary_markdown(headline: str, rows: list, provisional: bool) -> str:
    lines = ["**Provisional results**" if provisional else "**Registered outcome**", "", headline, "",
             "| Metric | Arm | Target | Result | Seeds | Verdict |",
             "|---|---|---|---|---|---|"]
    chosen = [r for r in rows if r["criterion"]] or rows
    for r in chosen:
        lines.append("| " + " | ".join(md_cell(r[k]) for k in
                     ("metric", "arm", "target", "result", "seeds", "status")) + " |")
    return "\n".join(lines)
