"""Pin a spec to its data (a content hash written into the TOML) and run the pin-stage checks.

    python -m rl_researcher.pin <spec>

A kind that has nothing to pin says so and exits 0.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from rl_researcher import atomic
from rl_researcher.cli import console, load_all, spec_parser


def _staged_checks(stage, spec, kind, config, out) -> list:
    """The registry's checks for a stage, with the project root the ones here need."""
    from rl_researcher import checks

    root = getattr(config, "root", None) if config is not None else None
    return list(checks.run_checks(stage, spec, kind, config, out=out, root=root))


def write_digest(spec_path: Path, digest: str) -> str:
    """Write the digest into the spec's ``sha256`` line, and say what happened.

    A kind may already have written it, in which case this finds the line correct and says so.
    A kind that computed a digest and left the file alone would mean the hash had to be pinned
    again before every run, which is the same as not pinning it.
    """
    text = spec_path.read_text(encoding="utf-8")
    new, n = re.subn(r'(?m)^(\s*sha256\s*=\s*)"[0-9a-f]*"', rf'\g<1>"{digest}"', text, count=1)
    if n != 1:
        return (f"no `sha256 = \"...\"` line in {spec_path.name} to write it into; the kind must "
                f"record it itself, or the spec needs one")
    if new == text:
        return f"{spec_path.name} already carries this digest"
    atomic.write_text(spec_path, new)
    return f"written into {spec_path.name}"


def main(argv=None) -> int:
    console()
    a = spec_parser(__doc__.split("\n\n")[0]).parse_args(argv)
    config, kind, spec, out = load_all(a.spec, a.out)
    spec_path = Path(spec.source_path or a.spec)
    digest = kind.pin(spec, spec_path)
    if digest is None:
        print(f"{kind.name} {spec.name}: nothing to pin")
    else:
        where = write_digest(spec_path, digest)
        print(f"{kind.name} {spec.name}: pinned {digest}")
        print(f"  {where}")
    findings = _staged_checks("pin", spec, kind, config, out)
    bad = 0
    for f in findings:
        print(f"  [{f.check}] {f.level}: {f.message}")
        bad += f.level == "error"
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
