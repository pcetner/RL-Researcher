---
name: rl-interpret
description: >-
  Read a finished run: what the numbers say, what the pictures say, where they disagree, and
  what the result cannot establish. Use when the run is over and a decision is waiting.
---

# rl-interpret

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

## What you are writing

Two authored regions, and nothing else. Everything above them regenerates; these two do not.

- **Reading** — what the numbers say, what the pictures say, and where they disagree. Every
  number cited as `[F####]` (C07).
- **Decision (human)** — you do not write in this one. It is the human's.

## Before writing a word

- Read `status` and the units table. An incomplete unit's numbers are about a smaller budget
  than the one registered, and the report says so at the top; do not read past that banner.
- Check the spread. The `±` is across seeds, not across runs, and on the action metrics the
  run-to-run spread is two to three times larger (L004). A mean that crosses a bar by less than
  that is not a bar-crossing.
- Check for a degenerate comparison. If every arm ties the reference, the report says so and no
  arm is marked; do not read the absence of a tick as a failure (L002).
- A winner with a collapsed latent is not a winner. Read the floor beside the headline.

## Writing it

- Say what the run found in the terms the spec registered, then what it did not settle.
- **A number in this region cites the finding that produced it.** If a number you want to state
  has no ledger row, it was computed beside the runner, and it is not yet a number (L007).
- Name what the result cannot establish. Every measurement in this project has a paragraph
  saying so, and they are the paragraphs that have aged best.
- A null is a result. Say which family of explanation it implicates and what would separate
  them; that is a diagnosis or a measurement, not another study.

## Afterwards

`decide` records the human's ticked box into the ledger. It exits 1 if no box is ticked, which
is the correct outcome for a decision nobody has made yet.
