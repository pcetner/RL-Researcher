"""Validate a spec and run the pre-run checks.

    python -m rl_researcher.check <spec>

Exits 1 on an invalid spec or a check at error level; warnings are printed and pass.
"""

from __future__ import annotations

import sys

from rl_researcher.cli import console, load_all, spec_parser
from rl_researcher.findings import collect, errors
from rl_researcher.spec import SpecError, spec_fingerprint


def main(argv=None) -> int:
    console()
    a = spec_parser(__doc__.split("\n\n")[0]).parse_args(argv)
    try:
        config, kind, spec, out = load_all(a.spec, a.out)
    except SpecError as exc:
        print(f"invalid spec: {exc}")
        return 1
    units = kind.units(spec)
    print(f"{kind.name} {spec.name}: {len(kind.arms(spec)) if hasattr(kind, 'arms') else '?'} arm(s) x "
          f"{len(spec.seeds)} seed(s) = {len(units)} unit(s), {len(spec.metrics)} registered metric(s); "
          f"fingerprint {spec_fingerprint(spec)}")
    print(f"outputs -> {out}")
    findings = collect(kind, spec, config, out)
    for f in findings:
        print(f"  [{f.check}] {f.level}: {f.message}")
    if not findings:
        print("checks: nothing to report")
    bad = errors(findings)
    if bad:
        print(f"{len(bad)} error(s); `run` would refuse this spec (exit 4) unless passed --no-check")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
