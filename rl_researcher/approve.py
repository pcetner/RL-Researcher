"""Record the human's approval of a gated run.

    python -m rl_researcher.approve <spec> --quote "<what the human said>" [--session ID] [--by NAME] [--note TEXT]

Only after an explicit yes for this spec, in this session. The file is bound to the spec's
fingerprint, so editing the spec afterwards voids it. Writes nothing when the run is not gated.
"""

from __future__ import annotations

import sys

from rl_researcher.cli import load_all, spec_parser
from rl_researcher.gate import decide, write_approval


def main(argv=None) -> int:
    p = spec_parser(__doc__.split("\n\n")[0])
    p.add_argument("--quote", required=True, help="the sentence in which the human approved this run")
    p.add_argument("--session", default="", help="the session or conversation the yes was given in")
    p.add_argument("--by", default="the human, in chat")
    p.add_argument("--note", default="")
    a = p.parse_args(argv)
    if not a.quote.strip():
        print("an approval needs the quote of what the human said")
        return 1
    config, kind, spec, out = load_all(a.spec, a.out)
    d = decide(spec, kind, config, out=out)
    if not d.gated:
        print(f"{spec.name} is under the gate line ({d.cost.describe()}); no approval needed, nothing written")
        return 0
    path = write_approval(config, spec, d.cost, quote=a.quote, session=a.session, approved_by=a.by, note=a.note)
    print(f"approval written: {path}\n  estimate at approval: {d.cost.describe()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
