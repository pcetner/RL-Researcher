# A project from nothing

Thirty minutes, an empty directory, and a kind of your own by the end of it. The README's first
run uses the toy kind that ships with the package; this one writes a kind, because that is the
extension point and everything else is arranged around it.

The example is deliberately not machine learning: it measures how long a sorting algorithm takes
at a few input sizes. It has arms, seeds, a metric with a bar and a metric compared against
another arm, which is the whole shape of a study without needing a GPU.

Read [Concepts](concepts.md) first if you have not. This uses *unit*, *arm*, *bar* and
*fingerprint* without redefining them, and [Writing a run kind](run-kinds.md) is the reference
this tutorial is the tour of.

## 1. The directory

```sh
mkdir sorting && cd sorting
pip install "rl-researcher @ git+https://github.com/pcetner/RL-Researcher@<commit>"
```

Pin a commit. The pin is what ties a result on disk to the code that produced it.

## 2. The kind

`sorting_kind.py`, beside the config. Two attributes and two methods is the minimum; this adds
`unit_class` because the two arms do not cost the same, which the cost estimate needs to know.

```python
import random
import time

from rl_researcher.kinds import BaseKind, UnitResult, units_from
from rl_researcher.spec import MetricRegistry, RunSpec


class SortKind(BaseKind):
    name = "sort"
    registry = MetricRegistry(
        known={
            "seconds": "Wall time to sort one list of the registered length.",
            "comparisons": "How many element comparisons the sort made.",
        },
        titles={"seconds": "Seconds", "comparisons": "Comparisons"},
    )

    def units(self, spec: RunSpec) -> list[str]:
        return units_from([a["name"] for a in spec.extra["arms"]], spec.seeds)

    def unit_class(self, spec: RunSpec, unit: str) -> str:
        """What makes two units cost about the same.

        The throughput table is keyed on this, so an estimate for a list of a thousand never
        predicts the runtime of a list of a million. Include everything that changes the
        runtime and nothing that does not.
        """
        arm = unit.split("/seed", 1)[0]
        size = next(a["size"] for a in spec.extra["arms"] if a["name"] == arm)
        return f"sort/{size}"

    def run_unit(self, spec, unit, prepared, ctx) -> UnitResult:
        arm = next(a for a in spec.extra["arms"] if a["name"] == ctx.arm)
        rng = random.Random(ctx.seed)
        data = [rng.random() for _ in range(int(arm["size"]))]

        ctx.beat("running", step=0, elapsed_seconds=0.0)
        started = time.time()
        comparisons = 0
        if arm["algorithm"] == "insertion":
            out = list(data)
            for i in range(1, len(out)):
                j = i
                while j and out[j - 1] > out[j]:
                    out[j - 1], out[j] = out[j], out[j - 1]
                    comparisons += 1
                    j -= 1
                if j % 200 == 0:
                    # At least every `cadence.heartbeat_seconds`, and on every change of
                    # state. A run that goes quiet is indistinguishable from a run that hung.
                    ctx.beat("running", step=i, elapsed_seconds=time.time() - started)
        else:
            sorted(data)
            comparisons = len(data)

        elapsed = time.time() - started
        return UnitResult(
            arm=ctx.arm, seed=ctx.seed, status="complete",
            steps=int(arm["size"]), max_steps=int(arm["size"]), seconds=elapsed,
            metrics={"seconds": elapsed, "comparisons": float(comparisons)},
        )
```

Two things the framework will hold you to, both worth knowing before you write a real kind.

**`status` must be honest.** A unit that ran out of time before finishing its step budget is
`"incomplete"`, not `"complete"`, and the report says so where a reader cannot miss it.

**`steps` and `seconds` are cumulative across a resume.** The throughput table divides one by
the other; a kind that reports only this session's seconds against the total steps records a rate
it never achieved, and every later estimate on that machine is low.

## 3. The configuration

`rl-researcher.toml`, at the project root. The framework finds it by walking up from wherever a
command is run.

```toml
[project]
name = "sorting"

[kinds]
sort = "sorting_kind:SortKind"

[out]
sort = "docs/sorts"

[gate]
ungated_wall_minutes = 5
```

`[kinds]` maps the `kind` field of a spec to `"module:ClassName"`. The module has to be
importable — here it is a file in the working directory, so it is.

## 4. The registration

`studies/insertion-vs-timsort.toml`. This is written **before** the data is touched, and that is
the whole point of it.

```toml
name = "insertion-vs-timsort"
kind = "sort"
seeds = [0, 1, 2]
hypothesis = """
Insertion sort is quadratic and Timsort is not, so on two thousand elements insertion will not
be faster than Timsort: it is registered against that comparison and is expected to miss it.
The result that would change the plan most is insertion clearing it, which would mean two
thousand elements is too short for the difference to show and every later comparison at this
size says nothing.
"""
conjunction = ["seconds"]

[[arms]]
name = "timsort"
algorithm = "builtin"
size = 2000

[[arms]]
name = "insertion"
algorithm = "insertion"
size = 2000

[budget]
max_steps = 2000
max_seconds = 20

[cadence]
heartbeat_seconds = 5
checkpoint_every_steps = 500
checkpoint_seconds = 30

[[metrics]]
name = "seconds"
baseline = "timsort, well under a hundredth of a second at this size"
direction = "lower"
compare_to = "timsort"
why = "The claim is about time, so this is the metric the run is for."

[[metrics]]
name = "comparisons"
direction = "report"
why = "Says whether a time difference is the algorithm or the machine."
```

`[[arms]]` is not a table the framework owns, so it arrives in `spec.extra["arms"]` unchanged —
which is what `units` and `run_unit` above read.

Note what the metrics do. `seconds` is registered against another *arm* rather than against a
fixed number, because "faster than Timsort" is the claim and a threshold in seconds would be a
claim about this laptop. `comparisons` is a `report` metric: measured because a reader needs it,
not because anything turns on it.

And note which way round the comparison runs. The hypothesis says insertion will **miss** this
bar, so the report is expected to mark it ✗ — that is the registration working, not the run
failing. A registration is a prediction, and a prediction that the treatment loses is as
falsifiable as one that it wins. What would be worth knowing is the cross not appearing.

## 5. Run it

```sh
python -m rl_researcher.check    insertion-vs-timsort
python -m rl_researcher.estimate insertion-vs-timsort
python -m rl_researcher.run      insertion-vs-timsort
python -m rl_researcher.report   insertion-vs-timsort
python -m rl_researcher.state
```

`check` prints what the spec registers and every finding, and should report nothing. Delete the
`baseline` line from the spec and run it again: C02 asks whether you have said what the reference
arm is expected to score, because registering a comparison means asking whether a difference is
possible from there. Put it back.

`estimate` says `budget-cap` and quotes the spec's own `max_seconds` per unit — twenty seconds,
six units, two minutes — because nothing has ever run on this machine and the gate will not guess
about hardware it has not measured. That is under the five-minute line, so the run starts without
asking.

Raise `max_seconds` to 120 and run `estimate` again: twelve minutes, over the line, and it exits
3 with the `approve` command to record a person saying yes. That is the gate, and it is the
reason the budget is worth setting honestly rather than generously. Put it back to 20.

After the first run the estimate reads `measured` and rests on what this machine actually did.

`report` writes `docs/sorts/insertion-vs-timsort/README.md` and `report.html`, and one ledger row
per arm and metric. Open the HTML — it embeds its own images and fetches nothing.

The summary should read close to *"`insertion` missed seconds"*, with `timsort` not judged at
all: the reference arm of a comparison is never judged against itself, because asking whether
the control beat the control gives "no" and would mark it failed for being exactly as good as
it is.

`state` writes `docs/STATE.md`: the run is now waiting on you.

## 6. Decide

Open the report, read it, and tick a box in its decision region:

```
<!-- authored: decision -->
- [x] **go** — the result is what the run was for; build on it
<!-- /authored -->
```

Then:

```sh
python -m rl_researcher.decide insertion-vs-timsort --note "quadratic, as registered"
```

That writes a `decision` row to the ledger and moves the run out of "Awaiting you". The command
refuses if no box is ticked, which is the correct outcome for a decision nobody has made.

Now regenerate the report:

```sh
python -m rl_researcher.report insertion-vs-timsort
```

Every table is rewritten and your tick and your note are still there, byte for byte. That
property — authored regions survive regeneration — is what makes it safe to re-run a writer, and
it is why the reading lives in the same file as the numbers.

## Where to go next

- **A metric with a real bar.** Register `comparisons` with `bar` and `direction = "lower"` and
  watch the scorecard mark each unit rather than each arm.
- **A guard or a check.** `guards(spec)` for a condition about the machine, `check(spec, ctx)`
  for anything knowable before the first unit. Both refuse at exit 4, before anything expensive.
- **Resume.** Write a checkpoint in `run_unit`, call `ctx.sidecar(step=..., elapsed_seconds=...)`,
  and interrupt a run halfway. The identical command continues it.
- [Writing a run kind](run-kinds.md) is the method-by-method reference for everything above.
- [Reference](reference.md) for the commands, the configuration and every on-disk format.
