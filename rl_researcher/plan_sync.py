"""Keep the plan's evidence in step with the ledger, without touching the plan's prose.

    python -m rl_researcher.plan_sync [--check]

`docs/PLAN.md` is the document that says what this project believes and why. The failure it
keeps having is that evidence gets retyped into it: a study finishes, a paragraph is added, a
later study revises the finding, another paragraph is added under the first, and after a few
rounds the section is a stack of amendments where the earliest paragraph is the most confident
and the least true.

So the plan marks the places where evidence goes::

    <!-- ledger: touches=D4 -->
    2026-09-06 · Study 4 · action_sensitivity_ratio 1.273 ± 0.027 (n=3) ✓ [F0110]
    <!-- /ledger -->

and this rewrites those regions, in date order, from the ledger. A superseded row stays,
struck through, because "we believed this until Study 5" is part of the argument. Every line
carries its finding id, so any claim can be traced back to the run that produced it.

Text outside the regions is never read and never written. The plan's prose stays exactly as
it was typed.

``--check`` writes nothing and exits 1 if a sync would change anything, which is what CI runs.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from rl_researcher import atomic
from rl_researcher.cli import console
from rl_researcher.config import Config, load_config
from rl_researcher.ledger import Finding, Ledger, open_ledger
from rl_researcher.regions import Region, find, join, split


def best_per_run(rows: List[Finding]) -> List[Finding]:
    """One row per run: the arm that did best on the metric, in the metric's own direction.

    A decision in the plan is not settled by twenty arms, it is settled by whether *any* arm
    could do the thing. Without this a region under D4 lists every arm of every study and says
    nothing; with it, the region is the sentence a reader wants: this study got this far, the
    next one got further.
    """
    best: dict = {}
    for r in rows:
        if r.value is None:
            continue
        cur = best.get(r.run)
        if cur is None:
            best[r.run] = r
            continue
        better = r.value < cur.value if r.direction == "lower" else r.value > cur.value
        if better:
            best[r.run] = r
    return list(best.values())


def lines_for(ledger: Ledger, *, touches: Optional[str] = None, metric: Optional[str] = None,
              run: Optional[str] = None, kind: Optional[str] = None, data: Optional[str] = None,
              arm: Optional[str] = None, passed: Optional[str] = None,
              best: Optional[str] = None) -> List[str]:
    """The evidence lines for one region, oldest first, superseded ones struck through.

    A region says what it wants in its marker, because "every finding that touches D4" is 266
    rows and answers nothing. ``metric=`` narrows to one quantity, ``best=run`` keeps the
    winning arm of each run, ``pass=true`` keeps only the rows that cleared their bar.
    """
    rows = ledger.query(touches=touches, metric=metric, run=run, kind=kind, data=data, unit=arm)
    rows = [r for r in rows if r.kind != "lesson"]
    if passed is not None:
        want = passed.strip().lower() in ("1", "true", "yes")
        rows = [r for r in rows if r.passed is want]
    if best == "run":
        rows = best_per_run(rows)
    rows.sort(key=lambda r: (r.date, r.run, r.unit, r.metric))
    gone = ledger.superseded
    out = []
    for r in rows:
        line = r.line()
        out.append(f"- ~~{line}~~" if r.id in gone else f"- {line}")
    if not out:
        out = ["_No findings touch this yet._"]
    banners = ledger.mixed(rows)
    return [f"> **{b}**" for b in banners] + ([""] if banners else []) + out


def sync_text(text: str, ledger: Ledger) -> str:
    """``text`` with every ``ledger:`` region rewritten. Everything else is returned unchanged."""
    parts = split(text)
    for i, p in enumerate(parts):
        if not isinstance(p, Region) or p.kind != "ledger":
            continue
        a = p.attrs
        body = "\n".join(lines_for(
            ledger,
            touches=a.get("touches"), metric=a.get("metric"), run=a.get("run"),
            kind=a.get("kind"), data=a.get("data"), arm=a.get("arm"),
            passed=a.get("pass"), best=a.get("best"),
        ))
        parts[i] = Region(kind="ledger", arg=p.arg, body=body)
    return join(parts)


def sync(plan_path: Path, ledger: Ledger, *, check: bool = False) -> Tuple[bool, str]:
    """Rewrite the plan's evidence regions. Returns ``(changed, unified diff)``."""
    plan_path = Path(plan_path)
    old = plan_path.read_text(encoding="utf-8")
    new = sync_text(old, ledger)
    if old == new:
        return False, ""
    diff = "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"{plan_path.name} (on disk)", tofile=f"{plan_path.name} (from the ledger)"))
    if not check:
        atomic.write_text(plan_path, new)
    return True, diff


def regions_in(plan_path: Path) -> List[Region]:
    return [r for r in find(Path(plan_path).read_text(encoding="utf-8")) if r.kind == "ledger"]


def untouched(config: Config, ledger: Ledger) -> List[str]:
    """Decisions that findings claim to touch but the plan has no region for.

    A finding that says it bears on D4 and a plan with nowhere to put it is the failure this
    module exists to stop, arriving one step earlier. Naming them is how the region gets added.
    """
    plan = config.path("plan")
    if not plan.is_file():
        return []
    have = {r.attrs.get("touches") for r in regions_in(plan)}
    want = {t for r in ledger.rows for t in r.touches}
    return sorted(w for w in want if w and w not in have)


def main(argv=None) -> int:
    console()
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--check", action="store_true",
                   help="write nothing; exit 1 if a sync would change the plan")
    p.add_argument("--plan", default=None, help="path to the plan (default: the configured one)")
    a = p.parse_args(argv)
    config = load_config()
    ledger = open_ledger(config)
    plan = Path(a.plan) if a.plan else config.path("plan")
    if not plan.is_file():
        print(f"no plan at {plan}; nothing to sync")
        return 0

    marked = regions_in(plan)
    if not marked:
        print(f"{plan} has no <!-- ledger: ... --> regions, so there is nothing to keep in step.")
        missing = untouched(config, ledger)
        if missing:
            print("  findings already point at: " + ", ".join(missing))
            print("  add a region under each, e.g.  <!-- ledger: touches=D4 -->\\n<!-- /ledger -->")
        return 0

    changed, diff = sync(plan, ledger, check=a.check)
    if not changed:
        print(f"{plan}: {len(marked)} evidence region(s), all in step with the ledger")
        return 0
    if a.check:
        print(f"{plan} is out of step with the ledger:")
        print(diff)
        print("run `python -m rl_researcher.plan_sync` to bring it into step")
        return 1
    print(f"{plan}: rewrote {len(marked)} evidence region(s)")
    print(diff)
    missing = untouched(config, ledger)
    if missing:
        print("no region yet for: " + ", ".join(missing))
    return 0


def _findings_line(f: Finding) -> str:
    return f.line()


if __name__ == "__main__":
    sys.exit(main())
