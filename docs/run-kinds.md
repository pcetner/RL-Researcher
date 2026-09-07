# Writing a run kind

A run kind is the object a consuming project supplies so the package can run its experiments. It
is a plain Python class. Subclass `rl_researcher.kinds.BaseKind`, set two attributes, and
implement two methods — `units` and `run_unit`. Everything else has a working default, though
most kinds also override `load` and `unit_class`.

The worked example throughout is `rl_researcher/examples/toy/kind.py`, which fits a line to noisy
points and is short enough to read in one sitting.

Read [Concepts](concepts.md) first. The terms *unit*, *arm*, *bar* and *fingerprint* are used
here without redefinition.

## The smallest kind

```python
from rl_researcher.kinds import BaseKind, UnitContext, UnitResult, units_from
from rl_researcher.spec import MetricRegistry, RunSpec

class MyKind(BaseKind):
    name = "my-kind"
    registry = MetricRegistry(known={"accuracy": "Fraction of held-out items classified correctly."})

    def units(self, spec: RunSpec) -> list[str]:
        return units_from([a["name"] for a in spec.extra["arms"]], spec.seeds)

    def run_unit(self, spec, unit, prepared, ctx: UnitContext) -> UnitResult:
        ...
        return UnitResult(arm=ctx.arm, seed=ctx.seed, status="complete",
                          steps=n, max_steps=ctx.max_steps, seconds=elapsed,
                          metrics={"accuracy": score})
```

Register it in the project's `rl-researcher.toml`:

```toml
[kinds]
my-kind = "mypackage.kinds:MyKind"

[out]
my-kind = "docs/my-runs"
```

A kind is instantiated with no arguments, so anything configurable must have a default. Options
that belong to one launch rather than to the experiment — which device, whether to waive a guard
— are best set as attributes by whatever constructs the kind.

## Required attributes

### `name: str`

The kind's identifier. It appears in the spec's `kind` field, in the configuration's `[kinds]`
table, in throughput rows and in ledger rows.

### `registry: MetricRegistry`

What this kind can measure. A spec registering a name outside it is refused when loaded. Supply
a definition for every metric; the definitions are printed beside the numbers in a report, and a
metric a reader cannot look up is a metric they will guess at.

## The two methods that matter

### `units(spec) -> list[str]`

The run's units, in order, as `"<arm>/seed<N>"` strings. `units_from(arms, seeds)` builds the
conventional order — every seed of the first arm, then the next arm.

The list is the run's plan. The gate multiplies its length by the estimated seconds per unit; the
runner iterates it; the status command reads one directory per entry.

### `run_unit(spec, unit, prepared, ctx) -> UnitResult`

Runs one unit. This is where a project's actual work happens.

`ctx` is a `UnitContext` and carries everything the unit needs from the framework:

| attribute | meaning |
|---|---|
| `unit`, `arm`, `seed` | which unit this is |
| `cell` | the directory to write into; already created |
| `progress` | the path of the heartbeat file |
| `log` | the run's logger; every line is flushed to `<out>/run.log` |
| `device` | the resolved `DeviceInfo`, or `None` |
| `max_steps`, `max_seconds` | the effective budget, after any command-line override |
| `resume` | whether a checkpoint may be continued |
| `fingerprint` | the spec's identity, to be stored in any checkpoint |
| `cadence` | how often to beat and to checkpoint |

and three methods:

- **`ctx.beat(status, *, step, elapsed_seconds, rate=None, eta_seconds=None, last=None,
  history=None, **extra)`** writes the heartbeat and lets any dashboard redraw. Call it at least
  every `ctx.cadence.heartbeat_seconds` and on every change of state — starting, resuming,
  moving into evaluation, stopping. A run that goes quiet is indistinguishable from a run that
  has hung.
- **`ctx.sidecar(step=..., elapsed_seconds=...)`** records that a checkpoint exists at a step.
  The payload is the kind's own — a study saves tensors, the toy kind saves a JSON dictionary —
  and the framework only needs to know the step, so status can be read without loading it.
- **`ctx.stop_requested()`** is true after a stop has been asked for. Check it between steps;
  when it is true, checkpoint, beat with status `"stopped"`, and raise `HotStop`.

`UnitResult` is what the unit produced:

| field | meaning |
|---|---|
| `arm`, `seed` | which unit |
| `status` | `"complete"`, or `"incomplete"` if the time cap hit before the step budget |
| `steps`, `max_steps`, `seconds` | what it did |
| `metrics` | the registered numbers, by name |
| `history` | series a dashboard draws, by name |
| `extras` | non-scalar evidence, such as a curve |
| `evidence` | image paths, relative to the run directory |
| `params`, `resumed_from_step` | bookkeeping |
| `extra` | anything else, merged into the result at the top level |

`status` must be honest. A unit that ran out of time before finishing its budget is
`"incomplete"`, and the report says so where a reader cannot miss it, because its numbers are
about a smaller budget than the one registered.

**`steps` and `seconds` are cumulative, not per session.** A unit resumed from step 400 that
runs 100 more reports 500 steps and the time both attempts took, with `resumed_from_step = 400`.
The throughput table divides the one by the other, so a kind that reports only this session's
seconds against the total steps records a rate it never achieved, and every later estimate on
that device is low — including the one the gate asks a person to approve. The toy kind carries
`elapsed_seconds` through its checkpoint for exactly this reason.

### `load(path) -> RunSpec`

Loads and validates a spec. `BaseKind.load` reads the TOML through the generic loader and puts
every table the framework does not own into `spec.extra`, which is enough for many kinds — the
toy kind reads its arms from `spec.extra["arms"]`.

A kind with substantial structure of its own is better served by a `RunSpec` subclass with real
fields, so that mistakes are caught at load time rather than at the first `KeyError` an hour into
a run. Two rules are worth keeping when you do:

- refuse keys no table declares. A misspelled knob that is silently dropped runs the default and
  reports it as the setting that was asked for.
- keep any budget expressed once. If a kind calls its steps something else, translate the name at
  load time rather than carrying two numbers that can drift apart.

## Methods with useful defaults

### `unit_class(spec, unit) -> str`

What makes two units cost about the same. The throughput table is keyed on it, so an estimate for
one architecture never predicts the runtime of another.

Include everything that changes the runtime and nothing that does not. A training study uses the
architecture, the step budget and the batch size. A closed-loop run distinguishes an arm that
plans from one that draws at random, because those are not the same second.

The default is `"<kind>/<max_steps>steps"`, which is enough only when every unit of a run costs
the same.

### `device() -> DeviceInfo | None`

What the units will run on. Return `None` and the framework probes: `nvidia-smi` first, then the
processor name. A kind that holds a machine-learning library usually knows better and should say
so, because the device name is half of the key the throughput table is stored under.

### `guards(spec) -> list[Guard]`

Conditions under which a run must not start. A `Guard` has a name, a callable that returns true
when it is blocked, and a message explaining the refusal.

A guard is asked once, before the first unit. Use one for a condition that is about the machine
rather than about the registration: another process holding the GPU, a service that must be
stopped. `run` exits 4 when a guard blocks.

### `check(spec, ctx) -> list[Finding]`

Everything that can be established before anything expensive starts: that the model file exists,
that the data matches the hash the spec registers, that a reference value needed to read the
result is set. Return `Finding(check, level, message)` with a level of `"error"` or `"warn"`.

Two callers ask, through the same collector: `check` reports and exits 1 on an error, and `run`
refuses and exits 4. `run` asks after the guards and before `prepare`, so a refusal costs
nothing — no model is loaded and no unit directory exists. `--no-check` waives it, and both the
findings and the waiver go into the run's own log.

Prefer a finding to an exception raised inside `run_unit`. Discovering halfway through the second
unit that the data was wrong wastes everything before it, and produces numbers that look fine.

### `pin(spec, path) -> str | None`

The content hash of the data the spec is registered against, if it has one. Returning `None`
means this kind has nothing to pin, which `pin` reports plainly.

### `prepare(spec, ctx) -> Any`

Work done once per process, before any unit: loading a snapshot, building a model, booting a
service. Whatever it returns is passed to every `run_unit` as `prepared`.

### `summarise(spec, results, out, ctx) -> dict`

Called after the last unit. Draw the run-level figures, write whatever the project's report
needs, and return the keys to merge into `<out>/results.json`. The framework has already put the
run name, the kind, the fingerprint, the commit, the device, the budget, the seeds, the per-unit
results and the missing units there.

### `read_result(result) -> dict`

One unit's `results.json` in the framework's shape. The default returns it unchanged.

Override it when a project has finished runs that predate this contract. Those files are
evidence; rewriting evidence so that a newer tool can parse it is exactly what the ledger exists
to make impossible, so the kind that understands the old shape translates on read instead.

### `curves(spec) -> list[CurveSpec]`

*Read by nothing yet: the dashboard is not built.*

Which series in a unit's `history` a dashboard should draw, with a title each, and optionally the
registered metric whose bar is the series' floor. A curve drawn with its floor shows a plateau
under the bar as a decision to make rather than as a line going along.

### `log_vocab() -> LogVocab`

*Read by nothing yet: the dashboard is not built.*

How the run's log should be read: substrings that mark a notable line and the tone each gets, and
a regular expression matching a routine step line so that bursts of them can be thinned. A log
that emits a step line every hundred steps is otherwise a screen of near-identical lines with the
one line explaining the stop already scrolled off.

### `estimator_name(metric) -> str`

The function that computes this metric. It is asked once per metric and goes into that metric's
ledger rows, so a kind that computes two metrics with two functions records both correctly.

It should be a real importable path. The rule it supports is that a number stated out loud is a
number the runner computed, and the way that rule fails is a number recomputed by hand beside the
runner with a subtly different definition.

### `instrument_for(metric) -> str | None`

*Read by nothing yet: the check that uses it is not built.*

The offline measurement that calibrates a metric, if one exists. Intended for the check that a
metric's instrument has been calibrated on this data before a run is pinned to it.

### `blocks(spec, summary, out, view) -> list`

*Read by nothing yet: the writers that would ask for these are not built.*

Extra blocks for a given view of an artefact, for a kind that has something to show which the
generic writer does not know about.

### Attributes read by name

Three more are looked up on a kind if it defines them, and ignored if it does not:

| name | read by | meaning |
|---|---|---|
| `log_name` | the runner | the run log's filename; the default is `run.log` |
| `gate_tags(spec)` | the cost estimate | tags for this run, matched against `gate.always_gated` |
| `new_data_minutes(spec)` | the cost estimate | minutes of new data collection this run implies, matched against `gate.ungated_new_data_minutes` |

The last two are the only way those two configuration settings do anything. A project that sets
`always_gated` or `ungated_new_data_minutes` and implements neither hook has set a threshold that
nothing will ever cross.

`figure_captions`, a mapping from a figure's name to its caption, is read by the report writer.

## Testing a kind

Write the tests against the project's real committed spec rather than against a fixture. A
fixture keeps passing after the registration has drifted, which is the case that matters.

Worth covering:

- the spec loads, and says what it says: the arms, the seeds, the budget, the bars;
- the units are the arms crossed with the seeds, in order;
- two units that cost differently get different unit classes;
- a metric outside the registry is refused, and so is a key no table declares;
- `check` returns an error finding when the data it needs is missing;
- if `read_result` is overridden, that a real committed result translates, and that translating
  twice changes nothing.

`tests/conftest.py` in this package builds a throwaway project on disk with the toy kind wired
up, which is the pattern to copy.
