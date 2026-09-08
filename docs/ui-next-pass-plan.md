# Project home and research UI: next pass

Status: implemented and validated. Based on user feedback and a read-only Auto-SM64 snapshot taken September 8, 2026. The existing uncommitted implementation is preserved and extended. Recovery defaults to Unknown unless an adapter supplies verified evidence through the new optional read-only inspection hook; no Auto-SM64 adapter was changed.

Validation: 550 tests passed; 26 targeted state/Home checks passed after the final findings refinement. Ruff, mypy, JavaScript syntax, wheel asset inspection, and browser checks passed. Browser review covered desktop/mobile, dark/light, metric help, visible calibration warnings, and draft preservation through Home navigation. The preview rejected a POST launch request with HTTP 403. All 421 copied source files still match the capture manifest. Preview: `http://127.0.0.1:7784/#home`.

## Design principle

Remove repetition before hiding information. The default home page must explain the project goal, current work, what needs a person attention, and why. A run must explain its full question, evidence, operational status, and next action without making the user hunt through disclosures.

The previous pass hid essential context, exposed internal estimate terminology, and mislabeled navigation as diagnosis.

## Real reference and findings

Source: an external Auto-SM64 checkout, read only. Snapshot: a local `autosm64-reference` folder kept outside this repository. `manifest.json` records source, capture time, file sizes, and SHA-256 hashes. 421 files, approximately 128 MB, including self-contained historical HTML, reports, figures, specifications, results, progress histories, findings, and throughput. No source execution, checkpoints, training data, ROMs, approvals, or process locks were copied.

The reference covers Study 3, Study 4, Study 5, Phase 4 planner versus random, reward diagnostics, reward candidates, and plannability. All 63 copied progress records say done. Historical data must never be presented as live activity or evidence that a checkpoint currently exists.

- Project goal: learn to play SM64 from frames without task-specific rewards or SM64 demonstrations in the training loop.
- The Phase 4 report records planner coverage of 23.000 ± 3.559 cells versus random's 324.667 ± 62.872, with three seeds. Good offline model metrics did not establish useful planning.
- The queue holds Phase 4b pending a decision about reward formulation (PLAN D5): no candidate cleared both the error-correlation and plannability screens. Phase 5 depends on that decision. This is a research hold, not a request for compute approval.
- Plannability reports calibration defects and limitations for latent-count novelty. Those warnings cannot be collapsed beneath an apparently successful aggregate.
- The queue explicitly distinguishes an approximately 31-minute screening estimate from an 80-minute budget bound because there are no engine-loop throughput rows. This is the real case behind the confusing budget-cap copy.
- README, generated state, queue prose, reports, and ledger can describe different revisions or dates. Show provenance and disagreements; do not silently treat a prose DONE label as a recorded decision.

## 1. Add a genuine project home

Make `/` open Home. Add a persistent Home link above runs and retain direct links to individual runs and tabs, including browser back/forward behavior.

Home order:

1. Project name, one concise declared goal, current milestone/question, and a link to the project plan.
2. Needs attention: research decisions/holds, failures requiring intervention, and compute approvals as distinct reasons. Each row has a concise explanation, affected work, and a specific action. Deduplicate multiple symptoms of the same run.
3. Happening now: active runs, completed/total units, heartbeat, remaining time where supported. Say explicitly when no runs are active.
4. Next work: queued runs with dependency or hold reason; no Start affordance that bypasses a research hold.
5. Recent findings: a small set of dated outcomes tied to the project question, with links to complete evidence and history.

Items 2, 3, 4 should be clickable, and lead to a child page with exact details.

For the Auto-SM64 snapshot, the leading attention item should be the unresolved reward decision and the runs it holds, supported by queue/PLAN links. It should not imply the current live project remains in the same state.

Add optional project metadata for goal, current focus, and plan link to configuration. Keep declarations separate from observed run state. Provide a compact missing-metadata state for other projects. Do not generate an unsupported completion percentage or infer scientific conclusions from run counts. Validate plan links as local project resources or allowed external links.

## 2. Restore the visual character

Use Auto-SM64's earlier HTML as the starting point: quiet green-gray surfaces, fine borders, compact aligned tables, clear typography, restrained status colors. Reduce stacked cards and oversized empty space.

Use a clean humanist sans with more character than the current system stack; evaluate locally bundled Source Sans 3 as the first candidate. Bundle a licensed font asset during implementation, include its license, and retain a system fallback. Use tabular numerals for metrics and monospace only for identifiers/code. Check actual long research text before settling the type scale.

Primary buttons: charcoal on light backgrounds and pale neutral on dark backgrounds. Secondary buttons: neutral outline or subtle fill. Separate action, focus, status, and scientific chart tokens so changing button color does not alter arm identity or warning meaning. No purple primary buttons. Preserve clearly visible keyboard focus and readable contrast in both themes.

## 3. Keep the complete hypothesis visible

Remove sentence splitting and the full-hypothesis disclosure. Render the full registered hypothesis with its paragraphs and emphasis intact, at a readable line length. Keep status/actions above it so a long question does not hide operational controls. Eliminate duplicated hypothesis text elsewhere in the same view. A concise optional title can orient the user but never replace the registered text.

## 4. Make failures understandable and recovery explicit

Replace the bare identifier/error pair with a failure summary containing affected variant and seed, failed stage if known, the exact recorded error, and a supported next step. Distinguish recorded evidence from interpretation. Missing diagnostic evidence means 'Cause not yet identified', not an invented explanation.

The current `Test fixture: input file unavailable.` is a fabricated fixture error, not an actual diagnosis or a verified missing path. Label it as simulated while the toy fixture exists.

Replace 'Inspect failure' with an inline 'View error details' region containing the relevant error/log excerpt and recovery evidence. A separate 'Open failed unit' link may navigate directly to and highlight that unit; it must not merely open the general Units view.

Add 'Recoverable?' beside progress, heartbeat, runtime, and cost. Support Yes / No / Unknown with a short reason. A checkpoint file alone is insufficient for Yes: the adapter must establish usable checkpoint state and relevant compatibility. Distinguish resuming saved work from restarting a failed unit. Mixed runs show a count, such as '2 of 3 interrupted units can resume', with per-unit reasons. Keep scientific action-recoverability metrics separate from operational recovery terminology.

Use precise actions: Resume run, Retry failed units, or Open error details as supported by the backend. Enforce existing approvals, holds, and compatibility checks. Never advertise a recovery operation the adapter cannot perform.

## 5. Explain estimates and approvals in ordinary language

Keep estimate information visible in a compact grid: completed units, estimated remaining, last heartbeat, estimated runtime, estimated cost, Recoverable? Under the grid, show the estimate source and material uncertainty without a dropdown.

Replace 'Budget cap · no measured throughput' with a statement such as 'Runtime not measured yet. Maximum configured time: 80 min.' If a separate planning estimate exists, label it separately with its source; do not present the upper bound as measured ETA. Keep elapsed wall time distinct from summed unit compute time.

Replace 'Approval required ... over the 5 min line' with 'This run needs permission to use up to 6 minutes of compute. Your automatic-start limit is 5 minutes.' Substitute 'estimated' for 'up to' when it is a prediction rather than a cap. Show cost, affected machine, and the exact threshold when relevant. Explain that approval authorizes compute, not acceptance of the scientific result.

Use 'Approve compute' followed by an explicit Start/Resume action, preserving current approval semantics. Show any remaining research hold or launch prerequisite after approval. Quote the remaining work for recovery where the cost engine supports it; otherwise identify a full-run bound as such. Display unknown cost/time as unknown, never zero. Invalidate stale quotes and approvals after material spec changes.

Remove the Estimate details disclosure. Show concise reasons and basis directly. Only raw diagnostic payloads/provenance belong behind optional technical detail.

## 6. Restore metric help on hover

Use one aligned comparison table: metrics as rows and variants as columns. Keep values, sample counts, thresholds, direction, and pass/fail or unassessed state immediately visible. Avoid repeating the same definition for every variant.

Restore a help popover on the metric label: hover with a pointer, focus with a keyboard, tap/click to pin on touch, Escape/outside click to close. It contains the definition, formula, and interpretation; it must stay on screen and remain reachable when hovered. Do not use native title as the only help mechanism.

Keep calibration problems, missing seeds, incomparable datasets/budgets, and provisional conclusions visible beside affected evidence. Registered outcome and custom authored interpretation must remain distinct. Preserve custom reports, figures, and custom decision choices; a framework metric table cannot replace domain-specific evidence such as the plannability screens.

## 7. Implementation sequence

1. Reference fixtures: derive a portable replay dataset from the copied JSON/spec/report data, retaining provenance and custom evidence. Do not import Auto-SM64 code or run its adapters against the source checkout. Replay actions cannot launch compute or mutate the original project. Add clearly labeled simulated interruption variants only for states absent from the snapshot.
2. Data contract: extend configuration and the board response for project context, attention reasons, dependency holds, estimate basis, recovery evidence, and relevant diagnostic excerpts. Reconcile state/ledger/queue semantics and add regression tests before rendering new action labels.
3. Navigation and Home: implement stable routes and browser history; aggregate attention and activity; preserve in-progress decision drafts through Home/run/tab navigation and refresh.
4. Run overview: full hypothesis, exact errors, supported recovery actions, visible estimates, explicit compute approval. Keep existing decision recording and stale-write protection.
5. Results and visual system: restore hover/focus/tap help, compact comparison layout, old surface palette, revised font, and neutral action tokens. Apply matching styles to standalone reports where appropriate.
6. Browser validation and documentation: exercise the real snapshot and simulated faults; update usage docs and replace the toy-first review preview with the grounded project reference.

Likely implementation areas: `config.py`, `artefacts/state.py`, `board_view.py`, `serve.py`, `ui/board.js`, `ui/board.css`, `blocks/live.py`, `style.py`, and adapter/status/cost contracts as required. Keep domain-specific conclusions in project metadata or authored reports rather than hardcoding Auto-SM64 into the framework.

## Acceptance criteria

- Opening the app lands on Home and reveals the goal, current focus, activity, and actionable blockers. Empty and incomplete project states remain useful.
- A research hold is distinguishable from a compute gate and cannot be cleared by compute approval.
- A full multi-paragraph hypothesis is visible without expansion; repeated explanatory text is removed.
- Failure details identify the affected unit, show exact available evidence, and state whether recovery is supported. Unknown remains unknown; no invented diagnosis or usable checkpoint.
- Estimate source, uncertainty, and approval consequences are understandable without opening a disclosure.
- Metric definitions work with mouse, keyboard, and touch; important evidence warnings remain visible.
- Real multi-seed studies, long hypotheses, custom decision choices, failed calibration, historical findings, and queued dependencies all render correctly.
- Layout is checked at desktop and narrow mobile widths, light/dark themes, keyboard-only navigation, and enlarged text. Tables may scroll within their own region; the page must not overflow.
- Refresh, Home navigation, browser back/forward, and tab changes preserve drafts and focus where appropriate. Existing optimistic concurrency and exactly-once decision behavior remain covered.
- Run relevant Python tests, JS syntax validation, lint/type checks, package-asset checks, and browser interaction checks. No training run is needed to validate the UI.
- Auto-SM64 receives no writes. Snapshot files remain labeled historical; simulated failures are explicitly identified; no replay control can invoke a real run.
