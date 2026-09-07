"""rl_researcher: tooling for research that an LLM runs and a human gates.

A consuming project supplies *run kinds* (what a unit of work is and how to run it) and gets
back the discipline around them: pre-registered specs with fingerprints, locks and heartbeats,
hot-stop and resume, a cost estimate with a human gate, generated artefacts that keep the
human's and the LLM's authored regions, a findings ledger with provenance, a state page, and a
watcher that works with nobody in a session.

Every operation is a module run as ``python -m rl_researcher.<name>``.
"""

__version__ = "0.2.0"
