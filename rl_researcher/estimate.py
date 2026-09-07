"""What a run would cost, and whether it is over the gate line.

    python -m rl_researcher.estimate <spec> [--out DIR] [--max-steps N] [--max-seconds S] [--units a,b]

Exits 0 when the run may start now (under the line, or approved), 3 when it is gated and has
no approval. Prints the estimate, the basis it rests on, and the reasons.
"""

from __future__ import annotations

import sys

from rl_researcher.cli import console, load_all, spec_parser
from rl_researcher.gate import decide


def add_budget_args(p) -> None:
    p.add_argument("--max-steps", type=int, default=None)
    p.add_argument("--max-seconds", type=float, default=None)
    p.add_argument("--units", default=None, help="comma-separated arms or unit ids this machine takes")


def main(argv=None) -> int:
    console()
    p = spec_parser(__doc__.split("\n\n")[0])
    add_budget_args(p)
    a = p.parse_args(argv)
    config, kind, spec, out = load_all(a.spec, a.out)
    units = a.units.split(",") if a.units else None
    d = decide(spec, kind, config, out=out, max_steps=a.max_steps, max_seconds=a.max_seconds, units=units)
    print(f"{kind.name} {spec.name}: {d.cost.describe()}")
    for r in d.reasons:
        print(f"  gated: {r}")
    if not d.gated:
        print("  under the gate line: may run now")
        return 0
    if d.approved:
        print(f"  approved: {d.approval_path} ({(d.approval or {}).get('approved_at', '?')})")
        return 0
    print(f"  needs an approval at {d.approval_path}; after the human says yes:\n"
          f"    python -m rl_researcher.approve {spec.source_path or spec.name} --quote \"<what they said>\"")
    return 3


if __name__ == "__main__":
    sys.exit(main())
