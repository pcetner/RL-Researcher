"""Write the project's state page.

    python -m rl_researcher.state [--json]

This is the first command of every session. It reads every spec, the status of every run, every
decision stub, the queue, the approvals, the ledger and the watcher's last tick, and writes
``docs/STATE.md`` with ``state.html`` and ``state.json`` beside it.

Exit 0 always: the page is a report, not a check. `status` is the command that exits non-zero.
"""

from __future__ import annotations

import argparse
import json
import sys

from rl_researcher.artefacts.state import build_state, write_state
from rl_researcher.cli import console
from rl_researcher.config import load_config


def main(argv=None) -> int:
    console()
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--json", action="store_true", help="print the view as JSON instead of writing files")
    a = p.parse_args(argv)
    config = load_config()
    if a.json:
        print(json.dumps(build_state(config).to_json(), indent=2, default=str))
        return 0
    md = write_state(config)
    view = json.loads((md.parent / "state.json").read_text(encoding="utf-8"))
    print(f"state -> {md}")
    print(f"  {len(view['waiting'])} awaiting a decision, {len(view['running'])} running, "
          f"{len(view['queued'])} queued")
    for w in view["waiting"]:
        print(f"  awaiting: {w['run']} — {w['outcome']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
