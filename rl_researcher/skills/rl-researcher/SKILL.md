---
name: rl-researcher
description: >-
  Run a pre-registered study, engine loop or measurement through RL-Researcher and hand the
  human a report to decide on. Use for any "which of these works better" question, and for the
  measurements that say whether such a question can be answered on the data at hand.
---

# rl-researcher

Four verbs, in order, and a human between the third and the fourth.

    check <spec>      what it registers, and what is wrong with it        0 / 1
    estimate <spec>   what it will cost here, and whether it is gated     0 / 3
    run <spec>        every unit with no result yet                       0 / 1 / 2 / 3 / 4
    report <spec>     the document, from what is on disk                  0 / 1

Then `state`, which is what a session opens first: what is waiting on a person, what is
running, what is queued, what was decided.

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

## Before a run

- `check` first, always. It reports what the spec registers and every finding; `run` refuses
  on an error finding before its first unit, at exit 4. `lint --list` says what is asked.
- `estimate` before anything long. Over the project's thresholds it exits 3 and wants an
  `approve` recording that a person said yes to *this* fingerprint.
- Instrument first. If the primary metric has a measurement that says whether its bar is
  reachable on this data, run that measurement before pinning (C01, C03). Six cells failing a
  bar by the same distance discriminate nothing.
- Three seeds, or `screening = true` and a report that says so (C05).

## While it runs

`<out>/dashboard.html` is rewritten on every heartbeat and refreshes itself; the index over
every run in the project is `docs/index.html`. Hand the human the page, not the log. `status`
exits 2 on a stale or failed unit — a stale unit is a hang until proven otherwise.

Ctrl-C once is a hot stop: it checkpoints and exits 0, and the identical command continues it.

## After it finishes

`report` writes the README and the page beside it and upserts the ledger. The reading and the
decision are authored regions and survive every regeneration. Say what the run found, cite
every number by its `[F####]`, and stop — the decision is the human's.

A null is a result. `rl_researcher/lessons.md` L020 is what a null looked like the last time one was
worth more than the run that produced it.

## What makes a run worthless

Read `rl_researcher/lessons.md`, which ships inside the package. It is twenty incidents, thirteen of which are now checks that ask
themselves. The other seven are the ones nothing can ask for you.
