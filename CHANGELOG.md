# Changelog

Every result this package writes now records the version that produced it, under
`rl_researcher` in a run's `results.json` and in the `framework` field of every ledger row. That
only says something if the version moves, so it moves here.

A consuming project pins a commit rather than a version — the pin is what ties a result on disk
to the code that produced it — so these entries are for reading a stamp back, not for resolving
a dependency.

## 0.2.0 — unreleased

Corrections from a review of the package against its own stated invariants. The through-line:
the unattended and installed paths were weaker than the interactive checkout path, and all of it
was invisible from a checkout.

### Fixed

- **The watcher wrote a report over every measurement it found.** `act` called `write_report`
  whatever the run was, so a finished measurement lost its Question, Method, Result and Verdict
  sections to a report's layout and gained a `registered` ledger row for a run that registered
  nothing. The dispatch on `artefact_kind` now lives in `artefacts.write_artefact_for`, which
  both the `report` command and the watcher use.
- **The watcher assumed a spec file was named after its run.** Every other path resolves a run
  by the `name` inside the file. A spec named anything else made the watcher's only action
  throw, with nobody there.
- **The skills, the pre-commit hook and `lessons.md` were in no wheel.** They sat beside the
  package rather than inside it, so `install_skills` was a silent no-op, `install_hooks` exited
  1, C11 fell through to a project file that need not exist, and C10 found no skills and passed.
  They now ship under `rl_researcher/`.
- **C10 failed open.** With no skill files to compare it returned no findings, which reads as a
  clean bill. It is an error now.
- **The `run`-stage checks were never asked at a run.** C06 and C09 were asked by `lint` and by
  the git hook only; C09, which says the canary still passes before an expensive run, had never
  been asked before one. `run` now refuses on a staged output directory (C06) as well.
- **`--no-gate` and `--allow-guards` left no record**, though `reference.md` claimed the first
  was logged. Both waivers are now written to the run's own log, as `--no-check` already was.

### Added

- `BaseKind` and `RunKind` are generic in the spec type, so a kind may declare the `RunSpec`
  subclass the documentation recommends without violating its own base. Every such kind in the
  first consuming project reported an override error per method, on a rule the docs told it to
  follow; the types now express the advice.
- `rl_researcher` in every run summary and `framework` on every ledger row: which version of
  this package computed a number. The commit already recorded is the *consuming project's*.
- `RunContext.framework`, so a kind stamps provenance from the context rather than asking git —
  the same rule `RunContext.commit` already followed.

## 0.1.0

First release. Pre-registered specs with fingerprints, locks and heartbeats, hot-stop and
resume, a cost estimate with a human gate, generated artefacts that keep their authored regions,
a findings ledger, a state page, and a watcher.
