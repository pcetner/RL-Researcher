---
name: rl-operate
description: >-
  Launch, watch, stop and resume a run; read `status`; install the watcher and the git hooks;
  work out whether something is hung. Use when a run exists and the question is about the
  machinery around it rather than about the result.
---

# rl-operate

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

## Launching

    python -m rl_researcher.run <spec> [--units a,b] [--max-steps N] [--no-gate]

Exit codes are the contract: 0 done or hot-stopped, 1 failed, 2 locked, 3 gated, 4 refused by a
guard or a check. `--units` is how a run is split across machines; the summary says which units
are missing.

## Watching

- `status <spec>` — where every unit stands. Exit 2 if any is stale or failed. **This is the
  authority on liveness**; a page that refreshes itself cannot exit 2.
- `<out>/dashboard.html` — rewritten on every heartbeat, refreshes itself, opens offline.
- `python -m rl_researcher.watcher` — one tick reads every run, acts on what changed, and tells
  someone. A finished run gets its report, its ledger rows and a rewritten state page without
  anyone asking. It starts nothing: `[watcher] launch` is off because starting queued work is a
  decision. `scripts/install_watcher.ps1` registers it as a logon task.

## Knowing what alive looks like

A healthy unit's `progress.json` is seconds old, its `step` climbs, and its rate is roughly
what the first unit logged. The heartbeat alone cannot separate a slow unit from a dead one
until it has been quiet for twice the interval, so `status` also reads the lock: it says
outright when the process that owned the directory is gone and which checkpoint to resume from.

## Stopping

Ctrl-C once: checkpoint, exit 0, resume with the identical command. Killing by hand has bitten
twice on this machine (`rl_researcher/lessons.md` L016) — `Get-Process python` does not match the Store
Python, and `kill -9 $!` under Git Bash kills the subshell. Find the pid with
`Get-CimInstance Win32_Process` filtered on the command line, stop it, and confirm.

## The hooks

`python -m rl_researcher.install_hooks` sets `core.hooksPath` so the hook in the tree is the
hook that runs. It refuses a commit that stages anything under a run whose lock is alive (C06).
A lock committed to the repository looks alive to every later checkout.
