# RL-Researcher

Tooling for research that an LLM runs and a human gates. A consuming project supplies *run
kinds* (what one unit of work is and how to run it); the package supplies the discipline
around them:

- pre-registered specs with a fingerprint, so a bar changed after the result is a visible diff;
- one on-disk contract for every run: a lock, a heartbeat per unit, a flushed log, hot-stop and
  resume, a status command that exits 2 when something is stale or failed;
- a cost estimate from measured throughput and a human gate before anything expensive starts;
- generated artefacts (markdown with an HTML rendering) that keep the regions the LLM and the
  human wrote in;
- a findings ledger where every number carries its provenance, and a PLAN document that is
  synced from it;
- a state page that says what is running, what needs a decision and what is queued;
- a watcher that regenerates and notifies with nobody in a session;
- Claude Code skills that make the workflow the default.

Every operation is `python -m rl_researcher.<name>`. Install the skills with
`python -m rl_researcher.install_skills`.

The first consumer is [Auto-SM64](https://github.com/pcetner/Auto-SM64).

```sh
pip install "rl-researcher @ git+https://github.com/pcetner/RL-Researcher@<sha>"
```

Development:

```sh
pip install -e ".[dev]"
ruff check rl_researcher tests && mypy rl_researcher && pytest -q
```
