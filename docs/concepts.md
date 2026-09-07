# Concepts

This page defines the vocabulary the package uses and says what each piece is responsible for.
Read it before [writing a run kind](run-kinds.md), because the kind is defined in these terms.

## Run, spec, arm, seed, unit

A **run** is one experiment: a question, a set of conditions, and a budget.

A **spec** is the run's registration, written as a TOML file before the run starts. It names the
hypothesis, the seeds, the metrics with their bars, the budget, the cadence at which progress and
checkpoints must be written, and which decisions of the project's plan the run bears on. The
class is `rl_researcher.spec.RunSpec`; a run kind may subclass it to add tables of its own.

An **arm** is one condition being compared. A **seed** is one repetition of an arm under a fixed
random seed. A **unit** is one `(arm, seed)` pair — the smallest amount of work that produces a
result. Unit identifiers are written `"<arm>/seed<N>"`, and a run's units are ordered every seed
of the first arm, then every seed of the second.

Distinguishing arms from seeds matters when reading a result. Two seeds clearing a bar and one
missing it is a different outcome from three seeds clearing it narrowly, and a mean over the
three hides which happened. The scorecard therefore plots one marker per unit, not per arm.

## Fingerprint

`spec_fingerprint(spec)` is a hash of the registered part of a spec: everything except where the
file was loaded from, and excluding any field still at its dataclass default.

Two consequences follow from the exclusion. Adding a new knob to the code with a default does not
change the identity of an experiment registered before that knob existed. Editing a bar, a seed
list or a budget does change it, which invalidates any approval or checkpoint bound to the old
fingerprint. The second is the point: a bar changed after seeing a result is then a visible
difference rather than a quiet edit.

## Run kind

A **run kind** is the object a consuming project supplies. It answers four questions the package
cannot answer for itself:

1. what the units of this run are,
2. how to run one,
3. what to do with the results,
4. what must not be true while it runs.

Everything else — the lock, the heartbeat, the checkpoint bookkeeping, the log, the status, the
cost estimate, the gate, the documents, the ledger — is the same for every kind and belongs to
the package.

A kind is named in the project's configuration as `"module:ClassName"` and is instantiated with
no arguments. The protocol is `rl_researcher.kinds.RunKind`; `BaseKind` supplies defaults for
everything a kind need not customise. See [writing a run kind](run-kinds.md).

## Metric registry

A `MetricRegistry` is what a project can measure: every metric name the runner computes, with a
definition, an optional formula in mathtext, and a human-readable title. A spec that registers a
name outside the registry is refused when it is loaded.

This is not a spelling check. A registration whose metric is never computed is a registration
that appears to commit to something and does not, and the failure is silent until the report is
written with a column of `n/a`.

## Bars, comparisons and directions

A metric declares a `direction` of `higher`, `lower` or `report`.

A `report` metric has no bar and is not judged. It is measured because a reader needs it, not
because anything turns on it.

A metric with a `bar` is judged against that number, strictly: `higher` means the value must
exceed the bar, not equal it.

A metric with a `compare_to` names another arm and is judged against that arm's mean **in the
same run**. Three rules follow:

- the reference arm is not judged against itself. Asking whether the control beat the control
  gives "no", which would mark the control as failed for being exactly as good as it is.
- an arm that lands exactly on the reference has **tied**, which is marked `=`. It is not a pass,
  because matching a control is not beating it; it is not a failure either.
- if every arm lands on the same value, the comparison separates nothing. The report says so and
  marks no arm, because the bar is not wrong and the arms are not failing — the measure did not
  move on this data.

A spec may also declare a `conjunction`: a list of metrics that must hold together. An arm that
clears three bars out of four has not partly succeeded if the registration asked for all four,
and the report states the outcome as the conjunction it is.

## Diverged and missing values

Three outcomes are kept apart when a metric is aggregated over seeds:

- **not a number** — not computed, because the metric does not apply to that arm. Excluded
  entirely; `n` counts what was measured.
- **infinite** — measured, and the measurement blew up. Counted as *diverged*, kept out of the
  mean so one runaway result cannot swallow the seeds that worked, and reported.
- **finite** — averaged.

A diverged seed never counts as a pass, whichever way the bar points. Without that rule an
infinite value clears any `higher` bar outright.

The spread reported beside a mean is the population standard deviation, and it is zero for a
single seed rather than undefined, because a report prints it either way.

## Unit lifecycle and the on-disk contract

Every kind writes the same files, so the status command, the dashboards and the watcher work
without knowing what a run computes.

A unit lives in `<out>/<arm>/seed<N>/` and passes through these states:

- **not started** — the directory has nothing in it.
- **running** — `progress.json` exists and is being rewritten at least every
  `cadence.heartbeat_seconds`. It carries the step, the rate, the estimated time remaining, and
  the last values of any curves.
- **stale** — the unit still claims to be alive but its heartbeat is older than twice the cadence
  plus thirty seconds. A stale unit is treated as hung until shown otherwise.
- **failed** — the heartbeat says so and carries the exception. The step it reached is kept,
  because where a unit died is most of the diagnosis.
- **done** — `results.json` exists. A result on disk outranks the heartbeat: the result is the
  proof.

Two rules make a run interruptible. A checkpoint is written at the registered cadence, with a
small `checkpoint.json` sidecar giving the step and the elapsed time so status can be read
without loading the payload. And `run` is idempotent: running the identical command again skips
units that have a result and continues those that have a checkpoint.

A lock file, `.study-lock.json`, records the process that owns the output directory. A second run
against the same directory is refused while that process is alive *on the same host*, and takes
the lock over otherwise — a lock written from another machine cannot be checked from this one. Two runs sharing a directory do not conflict loudly — they skip each other's finished
units and write the same summary twice, and the numbers can even agree, which is worse, because
nothing says the run happened twice.

## Throughput, cost and the gate

A finished unit appends a row to `docs/ledger/throughput.jsonl`: the kind, the unit class, the
device fingerprint, the seconds taken and the steps done. Recording is best-effort — a unit that
reports no elapsed time is skipped, and a failure to write the row is logged rather than allowed
to stop the run, because bookkeeping must never lose a result. The **unit class** is what makes two
units cost about the same — for a training study, the architecture, the step budget and the batch
size — and it is the kind's to define.

An estimate is the number of units still to run multiplied by the seconds each is expected to
take. That per-unit figure is the median seconds *per step* of the last few matching rows,
multiplied by the step budget, falling back to the median seconds per unit when no per-step
figure is available. Because the step budget is a factor, `--max-steps` does lower a `measured`
estimate; it is only under `budget-cap`, below, that it does not. It records a `basis` saying where that number came from:

| basis | meaning |
|---|---|
| `measured` | matching rows exist for this device |
| `assumed-slowest` | no rows for this device, rows elsewhere, and `gate.unknown_device = "assume-slowest"`; the slowest known is scaled up |
| `budget-cap` | no usable rows for this device; the spec's own `max_seconds` per unit is used. This is the default outcome, because `unknown_device` defaults to `"gate"` |
| `nothing-to-run` | every unit already has a result |

The **gate** compares the estimate against thresholds in the project's configuration, given in
wall-clock minutes and dollars so that they mean the same thing on every machine. A run under
every threshold starts without asking. A run over any of them refuses to start until an approval
file exists at `studies/approvals/<run>.<fingerprint>.toml`, recording who approved it, when, and
in their own words. Because the filename carries the fingerprint, editing the spec afterwards
voids the approval.

Command-line overrides are folded into the estimate, so a short trial run of an expensive spec is
usually under the line. Two qualifications: when the basis is `budget-cap` the estimate rests on
`max_seconds`, so `--max-seconds` is what actually lowers it and `--max-steps` alone does not; and
a device or a tag listed in `gate.always_gated` is gated whatever the estimate says.

Two consequences of estimating the work *remaining* are worth stating outright, because both are
deliberate and both look like holes.

A run that is half finished is half the estimate, so resuming one is often ungated where starting
it was not. That is the intended reading: the compute already spent is spent. It also means a
gated run can be walked under the line in pieces with repeated `--units`, which is the same
mechanism that splits a run across machines and cannot be closed without closing that too. The
approval is bound to the fingerprint rather than to the launch, so the honest answer is that the
gate asks about cost, not about resolve.

And `budget.max_seconds` defaults to eight hours, so under `budget-cap` — the basis on any
machine with no throughput rows yet — the estimate is eight hours times the unit count. A spec
that does not set its own `max_seconds` is therefore gated on a machine that has never run
anything, however cheap it really is. Running the toy kind, or the project's canary, once on a
new machine is what replaces that guess with a measurement.

## Artefacts, regions and blocks

An **artefact** is a document a run leaves behind: a report, a measurement, a diagnosis, the state
page. It is a markdown file with an HTML rendering beside it, and it has a fixed section order
for its kind, so a reader learns the shape once.

Each section is a **region**, marked in the file by HTML comments and therefore invisible when
rendered:

```
<!-- generated: headline -->
| metric | ctrl | var5 |
<!-- /generated -->

<!-- authored: reading -->
The variance hinge is doing the work; the covariance term is not. [F0142]
<!-- /authored -->
```

Regenerating an artefact rewrites every generated region from what is on disk and carries every
authored region across byte for byte, whitespace included. This is what makes regeneration safe
to run: if a re-plot could lose a reading, nobody would run one, and every page would drift from
the data it claims to describe.

A **block** is one piece of an artefact — a table, a scorecard, a log tail — that renders itself
twice, as markdown and as HTML. A block holds plain values only; no spec and no summary dictionary
is passed into one. Four rules are enforced by tests over every block:

1. every CSS class it emits is one it declares, since a page ships the stylesheet of exactly the
   blocks it used;
2. its markdown contains no HTML;
3. it fetches nothing, so the page opens with no network;
4. its HTML states every number its markdown states, so the file and the page cannot disagree
   about a result.

## The ledger

`docs/ledger/findings.jsonl` is an append-only file with one row per claim. A row carries the
value, the spread, the number of seeds, the bar, whether it passed, the estimator that computed
it, the commit, the data, the budget, the artefact that states it, and the plan decisions it
bears on.

There are four kinds of row:

| kind | written by | meaning |
|---|---|---|
| `registered` | the report writer | a pre-registered metric of one arm |
| `post-hoc` | measurement and diagnosis writers | measured after seeing the result, and marked as such |
| `decision` | `decide` | what a person chose, from a ticked box |
| `lesson` | by hand | a rule that came out of an incident |

A registered row's identity is `(kind, run, unit, metric, fingerprint, commit)`. Regenerating a
report produces rows with identities already on file and writes none of them twice.

Nothing is ever edited or deleted. A claim that turns out to be wrong is superseded by a new row
naming the old one, and both stay: "this was believed until that" is itself part of the record,
and a ledger that quietly loses its mistakes cannot be used to check whether a plan was written
on good evidence.

The failure the ledger exists to prevent is knowledge being retyped as prose. A result is
measured, written into a report, summarised into a plan, restated in an amendment under the
first, and by the fourth retelling the number has drifted and the caveat has been dropped.

## Plan synchronisation

A project's plan document can mark places where evidence belongs:

```
<!-- ledger: touches=D4 metric=action_sensitivity_ratio best=run -->
<!-- /ledger -->
```

`plan_sync` rewrites those regions from the ledger, oldest first, each line carrying its finding
identifier, superseded lines struck through. Text outside the markers is never read and never
written, so the plan's prose is untouched. `plan_sync --check` writes nothing and exits 1 if a
synchronisation would change anything, which is the form for a continuous-integration job.

A region takes filters because an unfiltered one is useless: `touches=D4` alone can match
hundreds of rows. `metric=` narrows to one quantity and `best=run` keeps the winning arm of each
run, which turns a table into the chronological sentence a reader wants.

## The state page

`docs/STATE.md` is generated by `state` and lists, in this order: what is waiting on a decision,
what is running, what is queued, what was decided recently, and whether the machinery itself is
healthy.

The order is deliberate. A run that finished overnight and has been sitting unread is the most
expensive thing in a project, so it goes first; the health section, usually uninteresting, goes
last.

Nothing is typed into the page. A person's inputs are files — a ticked box in a report's decision
region, an entry in `studies/queue.toml`, an approval, a killed process — and the next `state`
reads them. An input that lives only in a chat is an input the next session cannot see.
