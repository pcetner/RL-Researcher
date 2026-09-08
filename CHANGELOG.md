# Changelog

Every result this package writes now records the version that produced it, under
`rl_researcher` in a run's `results.json` and in the `framework` field of every ledger row. That
only says something if the version moves, so it moves here.

A consuming project pins a commit rather than a version — the pin is what ties a result on disk
to the code that produced it — so these entries are for reading a stamp back, not for resolving
a dependency.

## 0.2.0 — unreleased

### Interactive workspace

- Clarified automatic refresh, widened the sidebar into name/action/age columns, and added
  relative waiting ages. Local Markdown and specifications open in an in-page document panel.
- Added an activity page, concise editorial questions/hypotheses, formatted outcome identifiers,
  and a sticky decision header with choice buttons and required reasons. Removed duplicate
  preview notices and unwritten report placeholders from findings.

- Added project Home with goal/focus, research holds, active work, compute gates, and recent
  report findings. Queue holds block board launches independently of compute approval.
- Restored complete hypotheses on Overview, visible estimate explanations, recovery evidence,
  and metric help on hover, focus, or tap. Added neutral controls, bundled Source Sans 3,
  and a compact mobile run menu.
- Added a read-only Auto-SM64 snapshot preview that uses real reports without importing its
  adapters or allowing mutations. Checkpoint sidecars no longer imply verified recovery.

- Replaced the stacked log/dashboard/report pane with Overview, Results, Units, and Logs.
  The overview uses the report's registered outcome and a compact arm comparison; per-seed
  winners, detailed evidence, and logs are secondary. Added search, collapsed history,
  state-specific controls, concise copy, and responsive layouts.
- Combined explicit decision selection and recording, with document revision checks,
  serialized submissions, and retry recovery. Decision rows now retain the selected choices.
  Ticking a box without recording it no longer removes a run from the waiting list.
- Added lightweight status responses and on-demand evidence. Refreshes preserve decision
  drafts, ignore responses for previously selected views, and report lost connections.
  Initial log reads are bounded and notable events appear before the raw log.
- Standalone dashboards share the outcome summary, collapse technical detail, and retain
  expanded sections across refreshes. Metric help supports keyboard and touch; report
  disclosures expand when printing. UI assets are included in installed wheels.

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
- **Nothing wrote `canary.json`.** C09 read it, the state page's Health section read it, and no
  code path in the package created it — so the check could only ever say "no canary result on
  file", in every project, and the page could only ever print "never run". `report` now records
  it when the spec is the canary, the run completed with no units missing, and git can name the
  commit. This is C10's defect a second time: a check that reports nothing when its input is
  absent (L021). Two things behind it:
  - **The canary could not recognise itself.** C09 exempted it by comparing `[canary] spec` with
    the spec's `name`, but the setting is documented as a path, so the two never matched and the
    canary warned about itself. All three spellings now resolve, through `resolve_spec`.
  - **A git error was read as "nothing changed".** A canary result naming a commit the
    repository cannot resolve silenced C09 for good. "Could not answer" is now distinct from
    "nothing changed", and the check says so.
- **`report` printed a page path that did not exist, for every measurement.** The command
  hardcoded `report.html` while `write_measurement` passed no `html_path` and so got
  `README.html` from the writer's default. Which page a kind gets is now answered by
  `artefacts.page_for`, beside the dispatch that decides which writer it gets, so the two cannot
  disagree again.

### Added

- **`python -m rl_researcher.serve`: one local page you can work from.** Edit any authored
  region, record the decision, write the approval, start or stop a run, regenerate the report,
  follow the log — from the board rather than from a text editor and four terminals. The
  markdown on disk stays the source of truth; the server is a view and an editor over those
  files and never a second copy of them, so nothing is stranded when it stops. It holds no
  research logic: every endpoint is a shim over the module that already did the job, and every
  refusal is the same refusal, in the same words, as the command line's. Binds 127.0.0.1, and
  rejects a write whose `Origin` says it came from anywhere but this page.
- `render.editable_article`, the renderer that keeps a document's authored regions addressable
  so a page can write back to one. Only `serve` calls it: the exported page is byte for byte
  what it always was, rather than a flag on the shared writer that could put an input into an
  archived report.
- `decide.record`, the whole of `decide` as a function, so a second caller records a decision by
  running the same code rather than reading the command's stdout back. `Finding.via` says which
  one did — for an audit, and for nothing else: the row is otherwise identical.
- `artefacts.writer.render_page`, the page half of `write` on its own, so a document edited one
  region at a time keeps its page in step without re-deriving every table around the paragraph.
- `StateView.ready` in `state.json`: the specs that have never been run. Not on the state page —
  a study nobody has started is not news — but a reader of the JSON that cannot see a spec until
  someone has run it has no way to offer to run it.
- `BaseKind` and `RunKind` are generic in the spec type, so a kind may declare the `RunSpec`
  subclass the documentation recommends without violating its own base. Every such kind in the
  first consuming project reported an override error per method, on a rule the docs told it to
  follow; the types now express the advice.
- `rl_researcher` in every run summary and `framework` on every ledger row: which version of
  this package computed a number. The commit already recorded is the *consuming project's*.
- `RunContext.framework`, so a kind stamps provenance from the context rather than asking git —
  the same rule `RunContext.commit` already followed.

### Removed

- `render.render_artefact` and `render.write_html`. Neither had a caller anywhere in the package,
  its scripts, its tests or its documentation; the one live markdown-to-HTML path is
  `artefacts.writer`. Two uncalled near-copies of a renderer beside the real one are how the next
  reader picks the wrong one.

## 0.1.0

First release. Pre-registered specs with fingerprints, locks and heartbeats, hot-stop and
resume, a cost estimate with a human gate, generated artefacts that keep their authored regions,
a findings ledger, a state page, and a watcher.
