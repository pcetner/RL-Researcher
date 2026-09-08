# Reference

Commands, configuration, and the format of every file the package writes.

## Commands

Every command is `python -m rl_researcher.<name>`. Commands that take a spec accept either a path
or a bare name, which is looked up in the project's specs directory. They all take
`--out DIR` to override where the run's outputs live.

The working directory may be anywhere inside the project; the configuration file is found by
walking upward from it.

### `check <spec> [--out DIR]`

Loads and validates the spec, prints what it registers, and runs any check-stage findings the
kind supplies.

`run` asks the same questions through the same collector, so this command tells you in
advance whether a launch will be refused. It reports; it does not launch, so an error here
exits 1 rather than the 4 a refused run exits with.

Exit 0, or 1 if a finding has level `error`.

### `pin <spec> [--out DIR]`

Asks the kind for a content hash of the data the spec is registered against and writes it into
the spec. A kind with nothing to pin says so and exits 0.

Exit 0, or 1 on a pin-stage error finding. C01 is asked here — whether the instrument
measurement that says a metric's bar is reachable on this data has a ledger row — because
pinning is the last moment before a registration is fixed.

### `estimate <spec> [--out DIR] [--max-steps N] [--max-seconds S] [--units A,B]`

Prints what the run will cost on this machine: the units still to run, the seconds each, the
total wall time and money, and the basis the estimate rests on. If the run is over the gate line
it also prints why, and the exact `approve` command to record a decision.

`--max-steps` and `--max-seconds` are folded into the estimate. When the basis is `budget-cap`
the estimate rests on the seconds cap, so `--max-seconds` is what lowers it and `--max-steps`
alone does not. `--units` restricts the estimate to the arms this machine will take when a run
is split across machines.

Exit 0 if the run may start, 3 if it is gated and has no approval.

### `approve <spec> [--out DIR] --quote "..." [--session S] [--by WHO] [--note N]`

Records that a person approved this exact spec, writing
`studies/approvals/<run>.<fingerprint>.toml`.

`--quote` is required and must not be blank: it is the sentence in which the approval was given.
The file also records the time, the operating-system user, the git commit, and the estimate as it
stood. Because the filename carries the fingerprint, editing the spec afterwards voids the
approval.

A run that is not over the line needs no approval, and running `approve` on one writes nothing
and says so. An approval file for a run nobody had to approve would make the gate's own record
untrustworthy.

Exit 0, or 1 if the quote is blank. Omitting `--quote` altogether is an argparse error, which
exits 2.

### `run <spec> [--out DIR] [--max-steps N] [--max-seconds S] [--units A,B] [--no-resume] [--allow-guards] [--no-check] [--no-gate]`

Runs every unit that has no result yet.

The order of operations is: assess the gate, take the lock, install the stop handler, start any
dashboard writer, ask the guards, run the checks, prepare once, then run each selected unit.
The summary is written to `<out>/results.json` when the last unit finishes.

Checks run before `prepare`, so a refusal costs nothing: no model is loaded, no unit directory
exists, and the lock is released. An error-level finding refuses the run. The alternative a
kind would otherwise write is a raise inside `run_unit`, which discovers halfway through the
second unit that the data was wrong and throws away everything in front of it.

Both the `check` and the `run` stages are asked, so a launch is refused by C06 — anything under
the output directory staged for commit while the run holds the lock — as well as by the kind's
own findings. The pre-commit hook asks C06 at the other end of the same mistake; the runner asks
it because the hook is not installed everywhere.

`--no-resume` starts every unit over, discarding checkpoints. `--allow-guards` runs even when a
guard is blocked. `--no-check` runs even when a check returned an error; the findings and the
waiver are both written to the run's own log, because the numbers the run produces stand on a
registration something objected to. `--no-gate` skips the cost gate; it exists because an
estimate can be wrong about a machine it has never seen.

All three waivers are written to the run's own log, and that is the point of them: a gate walked
past and a guard waived leave the same numbers on disk as a run that cleared both, so the log is
the only place that can say which happened.

`--units` names the arms or unit identifiers this machine takes, so a run can be split across
machines and the directories merged afterwards. The summary covers whichever units exist and
lists the ones that are missing.

Exit 0 on success or a hot-stop, 1 on a failure, 2 if another live process holds the lock, 3 if
gated without an approval, 4 if it was refused before the first unit — a guard was blocked, or
a check returned an error.

### `status <spec> [--out DIR]`

Prints where every unit stands, and who holds the lock if anyone does.

Exit 0, or 2 if any unit is stale or failed. This is the command a monitor calls on a timer;
units that have not started yet are not failures and do not affect the code.

### `state [--json]`

Writes `docs/STATE.md`, `state.html` and `state.json`, and prints a summary. `--json` prints the
view and writes nothing.

Exit 0 always. The page is a report, not a check.

### `report <spec> [--out DIR] [--no-ledger]`

Reads `<out>/results.json` and writes `README.md` and its page beside it (`report.html` for a
report, `README.html` for a measurement), together with a registered ledger row for every arm
and metric. Nothing is recomputed and nothing is re-run.

Safe to repeat, and meant to be: a page only the process that produced it can produce is a page
nobody can check. Regenerating rewrites the generated sections, leaves the reading and the
decision as they were written, and writes no ledger row whose identity is already on file.
`--no-ledger` writes the documents only.

`run` does not call this. A project that wants a report written as a run ends does it from its
kind's `summarise`.

When the spec is the project's canary, this is also what records that it passed, into
`<ledger>/canary.json` — see [`[canary]`](#configuration) below. The record is written here
rather than at the end of the run because the canary's exercise is `check` → `run` → `status` →
`report`, and a canary that produced numbers but could not be written up did not prove the
machinery works.

Exit 0, or 1 if the run has no `results.json` yet.

### `decide <spec> [--out DIR] [--note "..."]`

Reads the ticked box in the report's decision region, writes a `decision` row to the ledger, and
regenerates the state page. It needs a report to read, so run `report` first.

Exit 0, or 1 if no box is ticked. A decision the runner invents is not a decision, so the command
refuses rather than guessing, and prints the options the stub offers.

### `serve [--port 7777] [--open]`

One local page with the board on the left and one run on the right, from which every verb above
is a button: edit any authored region, record the decision, write the approval, start or stop a
run, regenerate the report, follow the log.

The markdown on disk stays the source of truth. It is an editor over those files and never a
second copy of them, so nothing is stranded when the server stops and every command still works
exactly as it did. There is no research logic in it: each endpoint is a shim over the module that
already did the job, and every refusal — an unticked box, a missing approval, a run over the gate
line — is the same refusal, in the same words, as the command line's.

Binds `127.0.0.1` only. A write is rejected when the `Origin` header says it came from anywhere
but this page, and when `Host` is not localhost: a server on your laptop is not private, and a
decision, an approval and a launch are not things another site gets to do on your behalf.

Stopping a run is a clean hot-stop on POSIX (SIGTERM, which the unit checks between steps). On
Windows there is no SIGTERM for a console process, so the button kills instead and says so — the
last cadence checkpoint survives, the step in flight is lost.

### `ledger_cli show [--touches D] [--metric M] [--run R] [--kind K] [--data D]`

Prints the findings matching a filter, oldest first, with a banner when the rows are not
comparable — when they span more than one data hash or budget.

`--touches` matches a plan label against the descriptive strings a spec actually writes, so
`--touches D4` matches `"D4 (reconstruction-free representation)"`. A label matches when it is
the whole entry or is followed by a character that is not part of a label, which keeps `D1` from
matching `D10`, while `§8` does cover `§8.4`.

### `ledger_cli backfill [--dry-run]`

Writes registered rows for every run that has a summary on disk and no rows yet. Safe to repeat:
a row whose identity is already on file is not written again.

Rows are dated by the oldest commit touching the result, following renames, so moving a finished
run onto a new layout does not redate everything it contains.

### `ledger_cli add --kind K --run R [--metric M] [--value V] [--note N] [--artefact PATH] [--touches D] [--supersedes F]`

Appends one row by hand. `--touches` and `--supersedes` may be repeated.

Exit 0, or 1 if `--kind registered` is given: a registered row must carry the run's fingerprint
and commit, so it comes from `report` or from `backfill`.

### `plan_sync [--check] [--plan PATH]`

Rewrites the plan's `<!-- ledger: ... -->` regions from the ledger and prints the difference.
Text outside the markers is never read and never written.

`--check` writes nothing and exits 1 if a synchronisation would change anything.

### `watcher [--once] [--interval S] [--quiet] [--dry-run]`

One tick reads the status of every spec under `[paths].specs`, diffs it against the last tick
on disk, and acts on what changed:

| what changed | what it does |
|---|---|
| a run reached `finished` | writes the report, upserts the ledger, rewrites the state page and the index, and says so |
| a run reached `FAILED` | says so, with the first line of the reason. Nothing is written |
| a run went `STALE` | says so once — the heartbeat has stopped, so the process is probably gone |
| a run is past its own projection by `[watcher] eta_overrun` | says so once, against the estimate the run made when it started rather than the one it is making now |

It starts nothing, and there is no code path in it that could. `[watcher] launch` is off by
default because starting queued work is a decision, and a decision needs a person; the watcher's
job is to make sure the person finds out there is one to make.

The last tick is `<paths.logs>/watcher.json` and the record is `<paths.logs>/watcher.log`, one
flushed line per event. The diff is against the file, not against memory, so a watcher that is
killed — a reboot, a sleep, a scheduled-task restart — picks up where it left off instead of
announcing every finished run in the project again.

`--quiet` logs every event and raises no toast. `--dry-run` says what changed and writes no
report, ledger row, state page or index. `--once` is a single tick, for a cron or a check.

The toast is the courtesy channel and the log line is the record: Windows toast varies by build,
by focus assist and by whether the session is interactive, so a failed toast is logged as one
line saying why and the watch continues. `scripts/install_watcher.ps1` registers `pythonw -m
rl_researcher.watcher` as a logon task for one project (`-Remove` unregisters it).

### `lint [--list] [--stage load,check,pin,run,lint,ci]`

Every check that needs no run in flight, over the whole project: the documents on disk, the
skills, the lessons file, and every spec under `[paths].specs`. Exit 1 on any error finding.

`--list` prints the catalogue — id, stage, the lesson it exists because of, and what it asks —
and stops.

Stages exist so a question is asked where its answer can still change a plan: `load` needs only
the spec, `check` runs before anything expensive, `pin` is the last moment before a registration
is fixed, `run` asks about this machine now, `lint` needs a document on disk, and `ci` is the
tooling checking itself. A check that raises is reported as a warning rather than propagating: a
bug in a check must not be able to refuse a run.

### `install_hooks [--repo DIR] [--uninstall] [--dry-run]`

Sets `core.hooksPath` to the shipped `.githooks`, so the hook in the working tree is the hook
that runs. A copy into `.git/hooks` goes stale and nobody finds out; `.git/hooks` is also not
shared by git, which is why every project ends up with a hook one person has and the others do
not.

The hook hard-refuses one thing — a commit staging a path under a run directory whose lock is
alive (C06) — and prints everything else `lint` finds without blocking. A hook that refuses on a
warning is a hook people learn to pass `--no-verify` to, and then C06 stops working too. A
project with its own `.githooks/pre-commit` keeps it.

### `install_skills [--dest DIR] [--dry-run]`

Copies every directory under the package's `rl_researcher/skills/` that holds a `SKILL.md` into
`~/.claude/skills`, overwriting. Four are shipped: `rl-researcher` (run one), `rl-design`
(write the spec), `rl-operate` (launch, watch, stop) and `rl-interpret` (read the result). They
share one byte-identical invariants block, which C10 checks.

### `colab_mirror --src DIR --dst DIR [--every S] [--slow-glob PAT] [--slow-every S] [--once]`

Mirrors a run's output directory onto a slower durable path while it runs, for working somewhere
ephemeral. Small files are copied every `--every` seconds and files matching `--slow-glob` every
`--slow-every`, each through a `.part` temporary so a half-copied file is never mistaken for a
complete one. `--once` is a single pass including the slow files, which is the final sync.

Nothing else in the package uses it.

## Configuration

`rl-researcher.toml` at the project root. Every key has a default, so the smallest useful file is
a `[project]` name and a `[kinds]` table.

```toml
[project]
name = "example"
python = "python"

[kinds]                                   # kind name -> "module:ClassName"
study = "mypackage.kinds:StudyKind"

[out]                                     # kind name -> output root, relative to the project
study = "docs/studies"

[gate]
ungated_wall_minutes = 45.0               # over this, a run needs an approval
ungated_money_usd = 0.0
ungated_new_data_minutes = 20.0
unknown_device = "gate"                   # or "assume-slowest"
unknown_device_factor = 2.0
always_gated = ["cloud", "full-snapshot"] # tags that are gated whatever the estimate
screening_seeds = 1

[devices."NVIDIA GeForce GTX 1650"]       # keyed by the device name as reported
hourly_usd = 0.0
kind = "cuda"                             # illustrative; the default is "cpu"

[paths]
specs = "studies"
ledger = "docs/ledger"
state = "docs"
plan = "docs/PLAN.md"
lessons = "docs/lessons.md"
logs = "docs/measurements/logs"
approvals = "studies/approvals"
queue = "studies/queue.toml"
diagnoses = "docs/diagnoses"

[watcher]
interval_seconds = 60.0
launch = false
notify = "toast"
stale_factor = 2.0
eta_overrun = 0.5

[canary]                                  # illustrative; the defaults are none and []
spec = "studies/canary.toml"
watched = ["src/thing"]
```

Thresholds are given in wall-clock minutes and dollars rather than in steps, so that they mean
the same thing on a laptop and on a rented machine.

An unrecognised key in `[gate]`, `[devices.*]`, `[paths]`, `[watcher]` or `[canary]` is an error
rather than a warning, because a misspelled setting that is silently ignored runs the default
and reports it as the setting that was asked for. Unknown keys under `[project]`, and unknown
top-level tables, are currently ignored.

`watcher.launch` is read and deliberately does nothing: it is off, and there is no code that
could act on it being on, because starting queued work is a decision. Everything else in the
file is consumed.

`canary.spec` may be written as a path (`studies/canary.toml`), as a bare name (`canary`), or
with the extension (`canary.toml`); all three name the same spec, and the run they name is
exempt from C09 rather than being asked to be fresher than itself. `canary.watched` is a list of
paths relative to the project root, compared against the *working tree* — so an uncommitted
change to a watched file makes the canary stale immediately, which is the intended reading.

## Spec format

```toml
name = "toy-line-fit"
kind = "toy"
seeds = [0, 1, 2]
hypothesis = """
What this run is for, what it predicts, and which result would change the plan most.
"""
decision_touches = ["D4 (the decision this bears on)"]
conjunction = ["slope_error", "r2"]       # metrics that must hold together
screening = false                          # true allows a single seed

[budget]
max_steps = 300
max_seconds = 60

[cadence]
heartbeat_seconds = 5                      # must be in [1, 300]
checkpoint_every_steps = 50                # must be >= 1
checkpoint_seconds = 30                    # must be >= 1

[[metrics]]
name = "slope_error"
baseline = "true slope 2.0"
direction = "lower"                        # "higher" | "lower" | "report"
bar = 0.1                                  # judged against a threshold ...
why = "Why this metric decides anything."

[[metrics]]
name = "r2"
direction = "higher"
compare_to = "ols"                         # ... or against another arm. Never both.
anchor_of = "earlier-run/arm"              # optional: this re-runs an earlier cell
why = "Why this one does."
```

A kind's own tables go in the same file. The toy kind, for instance, needs `[[arms]]` tables
with a `name` each, so the fragment above is the generic half of a spec rather than a
complete one.

Any table the framework does not own is put in `spec.extra` unchanged, so a kind can read its own
configuration from there, or subclass `RunSpec` and declare real fields.

Validation refuses: a missing name or hypothesis; an explicitly empty `kind`, though an absent
one defaults to `study`; no seeds, duplicate seeds, or no metrics; duplicate metric names; a
metric outside the kind's registry; a `report` metric carrying a bar or a `compare_to`; a metric
carrying both a bar and a `compare_to`; a conjunction naming a metric with neither; a step budget
below 1; a negative time budget; a heartbeat outside one to three hundred seconds; and a
checkpoint cadence below one step or below one second.

A time budget of exactly zero is allowed. It means "stop at the first check", which is how a run
proves it reports a time cap as *incomplete* rather than as a result.

## On-disk formats

### Layout

```
<out>/<run>/
    results.json              the run summary
    run.log                   every line, flushed as it is written
    .study-lock.json          who owns this directory
    README.md, report.html    the report, when a writer has produced one
    <arm>/seed<N>/
        progress.json         the heartbeat
        results.json          the unit's result; its existence means done
        checkpoint.json       the sidecar: step and elapsed time
        ...                   whatever else the kind writes
```

### `progress.json`

Rewritten at least every `cadence.heartbeat_seconds` while a unit is alive.

| key | meaning |
|---|---|
| `unit`, `arm`, `seed` | which unit |
| `status` | `starting`, `resumed`, `running`, `evaluating`, `stopped`, `incomplete`, `done`, `failed` |
| `step`, `max_steps` | progress |
| `elapsed_seconds`, `rate`, `eta_seconds` | timing |
| `budget_seconds`, `device` | context |
| `last` | the latest value of each curve |
| `history` | the curves, thinned to at most 120 points |
| `updated` | ISO-8601 UTC |
| `error` | present only on a failure |

`updated` is written as ISO-8601 UTC. A float epoch is still *read*, because runs that predate
that rule are still evidence, but the dispatch is on type: an epoch stored as a JSON string is a
parse failure rather than a heartbeat from decades ago.

`history` is thinned to at most 120 evenly spaced points, ends included, so a long run's
heartbeat stays small.

### `results.json`, per unit

The fields of `UnitResult`, plus `unit` and `wall_seconds`. Anything in the kind's `extra` is
merged at the top level; the declared fields win a collision.

The existence of this file is what "done" means. A result outranks the heartbeat.

### `results.json`, per run

```json
{
  "run": "...", "kind": "...", "fingerprint": "...", "git_sha": "...",
  "device": "...", "device_fingerprint": "...",
  "budget": {"max_steps": 300, "max_seconds": 60},
  "seeds": [0, 1, 2],
  "runs": [ ... one per unit ... ],
  "missing_units": [],
  "figures": {},
  "wall_seconds": 12.3
}
```

Whatever `summarise` returns is merged over this.

### `checkpoint.json`

`{"step": N, "elapsed_seconds": S, "saved": "<ISO-8601 UTC>"}`. The payload it accompanies is the
kind's own; the sidecar exists so status can be read without loading it.

Cleared once the unit has a result: the result is the proof, and the checkpoint has no further
use. A *failed* unit keeps its checkpoint, because that is what it will resume from.

### `.study-lock.json`

`{"pid": N, "host": "...", "run": "...", "started": "<ISO-8601 UTC>"}`.

A second run against the same directory is refused while that process is alive on that host, and
takes the lock over when it is not. Liveness is checked with `tasklist` on Windows rather than
`os.kill(pid, 0)`, because the Windows implementation of `os.kill` terminates the process it is
asked about.

### `studies/approvals/<run>.<fingerprint>.toml`

```toml
run = "study5-collapse-weights"
fingerprint = "8cf4424155e1e351"
approved_at = "2026-09-07T06:20:47+00:00"
approved_by = "the human, in chat"
quote = "yes, run it overnight"
session = ""
os_user = "..."
git_sha = "..."
note = ""

[estimate]
wall_seconds = 19512.0
money_usd = 0.0000
device = "NVIDIA GeForce GTX 1650"
basis = "measured"
units = 18
```

### `docs/ledger/throughput.jsonl`

One line per finished unit: the kind, the unit class, the device fingerprint and name, the
seconds per unit and per step, the steps, the status, the run, the unit and the date.

### `docs/ledger/canary.json`

One object: `commit`, `date`, `run`, `rl_researcher`, `device`, `wall_seconds` and `units`. What
C09 and the state page's Health section read.

Written by `report`, and only when three things hold — the spec is the project's canary, every
unit completed with none missing, and git can name the commit the run happened at. A canary run
that fails any of them records nothing and says which, on stdout. The third condition is the
load-bearing one: a result recorded without a resolvable commit reads as fresh forever, because
the staleness comparison cannot run against it, and a comparison that cannot run is not a
comparison that passed.

### `docs/ledger/findings.jsonl`

One line per claim. See [Concepts](concepts.md#the-ledger) for the fields and the four kinds of
row.

### Artefacts

A markdown file whose first line is a header comment naming the kind, the run, the fingerprint,
the commit, the data and the generation time, followed by H2 sections in the kind's fixed order.
Each section body sits inside a region:

```
<!-- generated: name -->  ...  <!-- /generated -->
<!-- authored: name -->   ...  <!-- /authored -->
```

The HTML file beside it is the same markdown with the markers stripped, rendered under one
stylesheet, with images embedded as data URIs. PNGs over 150 kB are re-encoded as JPEG so that a
report with many figures stays openable. Raw HTML in the markdown is escaped rather than passed
through: an artefact that can inject markup is one whose layout is no longer its kind's.

PNG re-encoding needs Pillow. It is not a declared dependency and is present only because
matplotlib pulls it in; without it a large PNG is inlined at full size rather than failing.

Section orders are declared in `rl_researcher/artefacts/layouts.py`. The report is: Summary,
Registered metrics, Units, Figures, Evidence, Provenance, Ledger, Reading, Decision (human).
The first seven are generated; the last two are authored.

## Exit codes

| code | meaning |
|---|---|
| 0 | fine, or a hot-stop, which is not a failure |
| 1 | an error finding, a failed run, or a refusal to act without an input |
| 2 | a stale or failed unit, or another live process holding the lock |
| 3 | over the gate line with no approval |
| 4 | the run was refused before its first unit: a guard blocked, or a check returned an error |
