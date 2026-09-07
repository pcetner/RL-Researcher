"""Write a finished run's report from what is on disk.

    python -m rl_researcher.report <spec> [--out DIR] [--no-ledger]

Reads ``<out>/results.json`` and writes ``README.md`` and ``report.html`` beside it, together
with a registered ledger row for every (arm, metric). Nothing is recomputed and nothing is
re-run: the numbers come from the results the run already wrote.

It is safe to run repeatedly, and that is the point. A page only the process that produced it
can produce is a page nobody can check, so every artefact here is reproducible from disk by a
second command. Regenerating rewrites the generated sections and leaves the reading and the
decision exactly as they were written.
"""

from __future__ import annotations

import json
import sys

from rl_researcher.artefacts import write_artefact_for
from rl_researcher.cli import console, load_all, spec_parser
from rl_researcher.ledger import open_ledger


def main(argv=None) -> int:
    console()
    p = spec_parser(__doc__.split("\n\n")[0])
    p.add_argument("--no-ledger", action="store_true",
                   help="write the documents but no findings (for a page you are only looking at)")
    a = p.parse_args(argv)
    config, kind, spec, out = load_all(a.spec, a.out)

    summary_path = out / "results.json"
    if not summary_path.is_file():
        print(f"no results.json in {out}: this run has not finished, so there is nothing to "
              f"report. `status {spec.name}` says where it stands.")
        return 1
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    ledger = None if a.no_ledger else open_ledger(config)
    before = len(ledger.rows) if ledger is not None else 0
    # Which document this run gets is the kind's to say, and the dispatch is shared with the
    # watcher so the two cannot disagree about it.
    path = write_artefact_for(spec, summary, out, kind=kind, ledger=ledger,
                              command=f"python -m rl_researcher.report {spec.name}")

    print(f"report -> {path}")
    print(f"page   -> {path.with_name('report.html')}")
    if ledger is not None:
        written = len(ledger.rows) - before
        print(f"ledger -> {written} new finding(s); {len(ledger.rows)} on file")
    return 0


if __name__ == "__main__":
    sys.exit(main())
