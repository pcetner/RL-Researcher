"""Pin a spec to its data (a content hash written into the TOML) and run the pin-stage checks.

    python -m rl_researcher.pin <spec>

A kind that has nothing to pin says so and exits 0.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rl_researcher.cli import console, load_all, spec_parser


def _staged_checks(stage, spec, kind, config, out) -> list:
    """The checks registry is built in a later milestone; until it exists this is empty."""
    try:
        import importlib

        checks = importlib.import_module("rl_researcher.checks")
    except ModuleNotFoundError:
        return []
    return list(checks.run_checks(stage, spec, kind, config, out=out))


def main(argv=None) -> int:
    console()
    a = spec_parser(__doc__.split("\n\n")[0]).parse_args(argv)
    config, kind, spec, out = load_all(a.spec, a.out)
    digest = kind.pin(spec, Path(spec.source_path or a.spec))
    if digest is None:
        print(f"{kind.name} {spec.name}: nothing to pin")
    else:
        print(f"{kind.name} {spec.name}: pinned {digest}")
    findings = _staged_checks("pin", spec, kind, config, out)
    bad = 0
    for f in findings:
        print(f"  [{f.check}] {f.level}: {f.message}")
        bad += f.level == "error"
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
