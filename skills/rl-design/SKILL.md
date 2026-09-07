---
name: rl-design
description: >-
  Write a pre-registration: the hypothesis, the arms, the metrics and their bars, the seeds and
  the budget — before any data is touched. Use when the question exists but the spec does not.
---

# rl-design

A spec is a promise made before the numbers exist. Everything here is about keeping it
answerable and keeping it honest.

<!-- invariants -->
## The hard rules

These are the same in every skill here, byte for byte. C10 fails if they ever differ.

- **The human decides.** Go, iterate, stop, and every gated launch. Tooling reports and
  refuses; it never decides and it never starts queued work.
- **A number you say out loud is a number the runner computed.** Import the estimator, never
  retype it. Ratios of sums, never means of ratios. Cite the ledger id.
- **A run is never silent.** Heartbeat at most every 60 s, flushed, into a log in the repo —
  never a session temp directory. `status` is the authority on liveness, not a page.
- **A study survives a shutdown.** Checkpoints on a cadence; the identical command resumes it.
- **Never commit a run's outputs while it is running.** Not `git add -A`, not once.
- **Register before you measure.** Metrics, bars and the direction of every comparison are in
  the spec before the data is touched. A bar changed afterwards is a different study.
- **Every artefact is reproducible from disk by a second command.** A page only its own
  process can produce cannot be restyled, regenerated after a crash, or checked by anyone.
<!-- invariants -->

## The shape of a spec

    name, kind, seeds, hypothesis, decision_touches
    [[metrics]]  name, direction, bar | compare_to, baseline, why
    [budget]     max_steps, max_seconds
    [cadence]    heartbeat_seconds, checkpoint_every_steps

## The hypothesis

Three things, in this order: what is expected, what would change the plan most, and why that
second thing is the interesting outcome. A hypothesis that only says what is expected has not
said what would count as evidence against it.

## The metrics

- **Every metric has a `why`.** Not what it measures — the registry says that — but why it is
  registered *here* (C13).
- **A bar is a claim that a difference is possible.** Before registering one, know what the
  instrument measurement has actually seen on this data (C01, C03).
- **A `compare_to` is a claim that the reference arm will score something a treatment can
  beat.** Write that expectation into `baseline`. A comparison every arm ties is a column of
  crosses that reads as evidence against every treatment (C02).
- **An anchor is re-run inside the study, not cited from an old one** (C04). The run-to-run
  spread on the action metrics is two to three times the seed spread.
- Prefer "every seed clears it" to "the mean clears it".

## The budget

`estimate` prices it. The gate is about cost, not ceremony: under every threshold it starts
without asking, over any of them it wants an approval naming this fingerprint. A budget chosen
so the run squeaks under a gate is the gate working on the wrong thing.

## Not a study

If the question is "can this be measured on this data at all", it is a **measurement**: no
arms, no seeds, no bars, and a document that says what it cannot show. If it is "why did that
run come out null", it is a **diagnosis**, and it carries a post-hoc banner (C12).
