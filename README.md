# RL-Researcher

A Python package for running pre-registered experiments, where the work is carried out by a
language model and the decisions are made by a person.

The package does not know what an experiment computes. A consuming project supplies a *run
kind*: an object that says what one unit of work is and how to run one. Everything around that
is here — loading and validating the registration, estimating what a run will cost, refusing to
start an expensive one without a recorded approval, taking the lock, writing the heartbeat,
checkpointing, resuming, reporting the status, generating the documents, and recording every
number with its provenance.

The first consuming project is [Auto-SM64](https://github.com/pcetner/Auto-SM64), which uses it
for representation-learning studies. That repository is private at the time of writing, so the
examples here are drawn from the toy kind that ships with the package.

## Installing

```sh
pip install "rl-researcher @ git+https://github.com/pcetner/RL-Researcher@<commit>"
```

Pin a commit rather than a branch. The pin is what ties a result on disk to the code that
produced it: if the dependency can move underneath a project, a finished run cannot be
reproduced from the two repositories alone.

Python 3.10 or later. The package depends on numpy, matplotlib and markdown-it-py, and on
`tomli` under Python 3.10. It does not depend on any machine-learning library; a project's
training code lives in that project.

## A first run

The package ships a *toy kind* that fits a line to noisy points. It exercises the whole
pipeline in about a second per unit, so a new machine can be checked end to end before it is
trusted with a long run.

Create a project directory containing `rl-researcher.toml`:

```toml
[project]
name = "example"

[kinds]
toy = "rl_researcher.examples.toy.kind:ToyKind"

[out]
toy = "docs/toy"

[gate]
ungated_wall_minutes = 45
```

Write a spec to `studies/toy-line-fit.toml`:

```python
from pathlib import Path
from rl_researcher.examples.toy.kind import write_example_spec
write_example_spec(Path("studies/toy-line-fit.toml"))
```

Then, from anywhere inside the project:

```sh
python -m rl_researcher.check    toy-line-fit    # validate the registration
python -m rl_researcher.estimate toy-line-fit    # what it will cost, and whether that is gated
python -m rl_researcher.run      toy-line-fit    # run it
python -m rl_researcher.status   toy-line-fit    # where every unit stands
python -m rl_researcher.report   toy-line-fit    # write the report and the ledger rows
python -m rl_researcher.state                    # what now needs a person
```

`run` writes one directory per unit under `docs/toy/toy-line-fit/`, a summary at
`results.json`, and a log at `run.log`. Interrupting it and running the same command again
continues from the last checkpoint; units that already have a result are skipped.

## What the package provides

**A registration with an identity.** A spec is a TOML file naming the hypothesis, the seeds, the
metrics with their bars, the budget, and the heartbeat and checkpoint cadence. `spec_fingerprint`
hashes the registered part, ignoring fields left at their defaults. A bar edited after a result
changes the fingerprint, which voids any approval or checkpoint bound to it.

**One on-disk contract for every kind of run.** A unit is one `(arm, seed)` pair and lives in
`<out>/<arm>/seed<N>/`. It reports progress in `progress.json`, proves it finished by writing
`results.json`, and can be resumed from a checkpoint whose step is readable without loading the
payload. A lock stops a second process on the same machine from writing into a directory a
live one owns. See [docs/reference.md](docs/reference.md#on-disk-formats).

**A cost estimate and a gate.** A finished unit appends a row to a throughput table, so after the
first run on a machine the estimate for the next one is measured rather than guessed. Thresholds
are wall-clock minutes and dollars, set per project, so they mean the same thing on every machine. A run over the line
refuses to start until an approval file records that a person agreed to it, and the approval is
bound to the spec's fingerprint.

**Documents that regenerate.** `report` writes a run's report from `results.json`, and can be
run again at any time. An artefact is markdown with an HTML rendering beside it. Its
sections are marked as generated or authored: regenerating rewrites the generated ones and
leaves the authored ones byte for byte, so a re-plot after a styling change cannot lose a
reading someone typed. The HTML embeds its images and fetches nothing, so it opens from a file
path with no network.

**A findings ledger.** `report` writes one append-only row per arm and metric, carrying the run,
the unit, the estimator, the commit, the data and the budget. Documents downstream cite the row's
identifier. Nothing is edited or deleted; a claim that turns out to be wrong is superseded by a
new row that names the old one, and both remain.

**A state page.** `docs/STATE.md`, generated, listing what is waiting on a decision, what is
running, what is queued, and what was decided recently. It is written, never typed into: a
person's inputs are files — a ticked box in a report, an entry in the queue, an approval — and
the next `state` reads them.

## What is not built yet

The package is incomplete. One thing is not present: **the diagnosis writer**. Its layout is
declared in `rl_researcher/artefacts/layouts.py` and C12 already refuses a diagnosis that does
not carry its post-hoc banner, but nothing writes one yet. `rl_researcher.artefacts` has the
report, the measurement, the state page, the live dashboard, the index and the shared
statistics.

Every hook on the run-kind protocol is read by something.

## Commands

Each is `python -m rl_researcher.<name>`. Exit codes are part of the contract, because a monitor
reads them.

| command | what it does | exit codes |
|---|---|---|
| `check` | validate a spec and report what it registers | 0, 1 on an error finding |
| `pin` | write the data's content hash into the spec | 0 |
| `estimate` | what a run will cost on this machine, and whether it is gated | 0, 3 if gated |
| `approve` | record that a person approved a gated run | 0, 1 on a blank quote |
| `run` | run every unit that has no result yet | 0, 1 failed, 2 locked, 3 gated, 4 refused |
| `status` | where every unit stands | 0, 2 if any is stale or failed |
| `report` | write a finished run's report and its ledger rows | 0, 1 if the run has not finished |
| `state` | write `docs/STATE.md`, `state.html` and `state.json` | 0 |
| `decide` | record a decision from a ticked box | 0, 1 if no box is ticked |
| `ledger_cli` | read the ledger, or backfill it from finished runs | 0, 1 on a hand-written registered row |
| `plan_sync` | rewrite a plan's evidence regions from the ledger | 0, 1 with `--check` when out of step |
| `watcher` | act on what changed since the last tick, and say so | 0 |
| `lint` | ask every check that needs no run in flight | 0, 1 on an error |
| `install_hooks` | point `core.hooksPath` at the shipped pre-commit hook | 0 |

`watcher` is the only one meant to run unattended. One tick reads every run's status, compares
it against the tick before, and does what a person would: on a run that just finished, the
report, the ledger rows, the state page and the index; on one that failed, went quiet, or blew
past its own projection, a line in `watcher.log` and a toast. It starts nothing —
`[watcher] launch` is off by default, because starting queued work is a decision.
`scripts/install_watcher.ps1` registers it as a logon task.

Two utilities sit outside that flow: `install_skills` copies the four Claude Code skills into
`~/.claude/skills`, and `colab_mirror` mirrors a running output directory onto a durable path
such as a mounted Drive.

## Checks

Thirteen questions, each asked at the stage where the answer can still change a plan, each
named on the lesson it exists because of. `lint --list` prints them;
[`rl_researcher/lessons.md`](rl_researcher/lessons.md), which ships inside the package, says what
each cost. `run` refuses at exit 4 on an error finding before its first unit; `check` reports
one and exits 1; the pre-commit hook refuses a commit that stages a path under a run whose lock
is alive.

C11 keeps the two lists honest in both directions — a check naming a lesson that is not on file
fails, and a lesson naming a check that is not registered fails — so neither can be tidied
without the other noticing.

Full argument lists are in [docs/reference.md](docs/reference.md#commands).

## Documentation

- [Tutorial](docs/tutorial.md) — an empty directory to a decided run, writing a kind on the way.
- [Concepts](docs/concepts.md) — the vocabulary, and what each piece is responsible for.
- [Writing a run kind](docs/run-kinds.md) — the extension point, method by method.
- [Reference](docs/reference.md) — commands, configuration, on-disk formats, exit codes.

## Development

```sh
pip install -e ".[dev]"
ruff check rl_researcher tests && mypy rl_researcher && pytest -q
```

The test suite runs the toy kind end to end and does not need a GPU, a network or any project
data.

## Licence

MIT.
