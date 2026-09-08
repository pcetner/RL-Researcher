# Lessons

Twenty-one things that went wrong, and what each one is now a rule about.

This file exists because rules stated without their history get argued past, and rules stated
*as* their history get misread. So every entry has both, and they are kept apart: **What
happened** is the incident, **Rule** is what to do, and **Check** names the check that asks it
automatically — or says `prose only` where nothing can.

C11 keeps the two lists honest in both directions: a check naming a lesson that is not here
fails, and a lesson naming a check that is not registered fails. Neither list can be tidied
without the other noticing.

Dates are when the incident was recorded, not when the code was written.

---

### L001 — 2026-09-06 — a bar nothing on the data can reach

**What happened.** Study 4 registered a floor on `participation_ratio_norm` of 0.10. That
statistic divides an effective dimension count by the latent width, so on a 256-d latent the
floor silently asks for 25.6 effective dimensions. Nothing measured had reached half of that.
Eighteen cells ran against it; every one failed by about the same distance, and the column
discriminated nothing. The same trap had already been avoided once for the action metric,
because `action_recoverability` was run before Study 3 — a pixel classifier is the upper bound
on what any latent can carry, and at chance it says no representation objective can do better.

**Rule.** Before pinning a study whose primary metric has an instrument measurement, run that
measurement on this data hash. A bar above everything the instrument has ever seen is a fault
in the bar.

**Check: C01**

---

### L002 — 2026-09-06 — a comparison nothing can win

**What happened.** Phase 4 registered `parked_fraction` against the random arm. Every arm
scored 0.000, and the three treatment arms were marked ✗ for not being *strictly* below zero.
A tie marked as a failure is worse than no mark at all, because it reads as evidence against
the treatment.

**Rule.** Registering a `compare_to` metric means asking what the reference arm will plausibly
score, and whether a difference in the registered direction is possible from there. Write that
expectation into the metric's `baseline` so the question has visibly been asked.

**Check: C02**

---

### L003 — 2026-09-06 — a floor is a fraction, and a linear rank is not a ceiling

**What happened.** Two traps in one number. `participation_ratio_norm`'s floor is a fraction of
`latent_dim`, so it scales with a width nobody was thinking about when the bar was chosen. And
the frames' own participation ratio — the obvious thing to compare it against — is a *linear*
statistic that a nonlinear encoder can and does exceed, so it is the scale of the problem and
never a ceiling. `latent-capacity` reports it as a reference for exactly that reason, and its
verdict says "far short" rather than "impossible".

**Rule.** Compare a bar against what the instrument has measured, in the units the bar is in.
Say what the comparison cannot establish.

**Check: C03**

---

### L004 — 2026-09-06 — the run-to-run spread is larger than the seed spread

**What happened.** Re-running Study 3's cells under identical conditions — same seed, same
data, same budget, only CUDA kernel nondeterminism differing — moved `action_sensitivity_ratio`
by 13%, 5% and 17% on one arm and +39%, −5%, −27% on another, against a between-seed spread of
2–11%. Study 3's headline was reported as a pass on a mean that crossed its bar by less than
that, and then failed to replicate. Rank metrics do not suffer this: `participation_ratio`
reproduced to 0.9%.

**Rule.** The `±` in a report is a seed spread, not a run spread. Prefer "every seed clears it"
over "the mean clears it", and **re-run the anchor inside the new study** rather than comparing
a new arm against an old study's number.

**Check: C04**

---

### L005 — 2026-09-06 — one seed read as a difference, in one branch of a taxonomy

**What happened.** Phase 4's `diagnose()` gated its coverage comparison on n >= 2 and returned
"unresolved" correctly. The very next branch printed "distribution shift" off a single seed in
a 30-decision smoke run, because that branch compared two arm means directly.

**Rule.** Three seeds unless the spec declares itself a screening pass, and when the reading is
code, every branch of it obeys that — one guarded comparison in a taxonomy is not a guarded
taxonomy.

**Check: C05**

---

### L006 — 2026-09-05 — a run's outputs committed while it ran

**What happened.** `git add -A` during a live run staged a half-written `results.json` and a
lock file. A lock committed to the repository looks alive to every later checkout, and the next
run on that directory is refused for a process that ended days ago.

**Rule.** Never stage anything under a run directory whose lock is alive. Not `git add -A`
while a run is in flight, ever. Asked at both ends of the mistake: the pre-commit hook refuses
the commit, and the runner refuses to launch over a directory that already has staged changes,
because the hook is not installed everywhere.

**Check: C06**

---

### L007 — 2026-09-06 — a number that reached a human from beside the runner

**What happened.** Every number that has reached a human wrongly reached them from analysis done
*beside* the run — a shell one-liner over a trace, a scratch script, a quick mean in a REPL —
where nothing checks the estimator against the one the runner uses.

**Rule.** A number you say out loud is a number the runner computed. Import the estimator,
never retype it; ratios of sums, never means of ratios. In an authored region, a line stating a
registered metric and a decimal cites the ledger id it came from.

**Check: C07**

---

### L008 — 2026-09-04 — an hour of silence

**What happened.** A screening ran for an hour with an empty log. Later the same week a pilot,
two collections and a gate ran for three hours with their output in a session scratchpad, and
the first anyone outside the session knew of it was being asked why there was no progress to
see. Separately, a CUDA OOM in an evaluation reached stderr only, and a study looked alive for
eight hours.

**Rule.** A run is never silent. Heartbeat and checkpoint cadence are configured and within
bounds; the log lives in the repo, flushed, never in a session temp directory; a failure is
written into the log and marks the unit failed. `status` is the authority on liveness, and a
stale unit is a hang until proven otherwise.

**Check: C08**

---

### L009 — 2026-09-05 — a canary that predated the change it was meant to catch

**What happened.** The canary is what says the machinery still works before an expensive run.
A canary result from before the last change to the runner, the metrics or the figures says
nothing about them, and one was read as though it did.

**Rule.** Before a gated run, the canary must have passed at a commit no older than the last
change to anything it exercises.

**Check: C09**

---

### L010 — 2026-09-06 — four copies of one rule

**What happened.** The hard rules were repeated across documents, and the copies drifted. The
copy being read when it mattered was the one that had drifted.

**Rule.** One invariants block, byte-identical across every skill file, between
`<!-- invariants -->` markers. Four copies of a rule is four rules.

**Check: C10**

---

### L011 — 2026-09-07 — a rule with no reason on file

**What happened.** Rules accumulated in a 409-line skill with their incidents embedded in
prose. Nothing connected a rule to what it cost, so a rule could be deleted as clutter and
nobody would know what had been given up.

**Rule.** Every check names the lesson it exists because of, and every lesson names its check.
Neither list is tidied without the other noticing.

**Check: C11**

---

### L012 — 2026-09-06 — a diagnosis that read like a prediction

**What happened.** A document written after seeing a result, laid out like one written before
it, is the single most misleading artefact this project can produce. The report layout exists
to make that impossible: the registered outcome is fixed before the run and sits first, the
reading is written after it and sits eighth.

**Rule.** A diagnosis carries its post-hoc banner and says, in its own words, which result it
was written after.

**Check: C12**

---

### L013 — 2026-09-05 — a metric with no definition and no reason

**What happened.** A metric list grows. A metric with no definition renders on the page as its
own variable name over an empty bubble; a metric with no `why` is one nobody has said out loud
why they are measuring, and it survives into the next spec by being copied.

**Rule.** Every registered metric is in its kind's registry, with a definition, a formula that
typesets, and a `why` in the spec.

**Check: C13**

---

## Prose only

These cost something too. Nothing can ask them automatically.

### L014 — 2026-09-06 — a rule stated as its history was read as its opposite

**What happened.** The dashboard paragraph opened by explaining that `dashboard --watch` had
been *replaced* after a stale watcher overwrote a page under review, and named redrawing only
in a subordinate clause. Read once, the moral is "do not build a watcher", so the engine loop
shipped with a heartbeat writer and no way to redraw its page. The gap was then noticed and
argued past: the plan for it says, in as many words, "there is no command to regenerate this
page", and used that as a reason to commit the HTML as a record rather than as a reason to
write the command.

**Rule.** State a rule as a rule. Put its history after it, or in this file.

**Check: prose only**

---

### L015 — 2026-09-06 — a page only its own process could produce

**What happened.** A styling fix made while a run was in flight could not reach the run's page,
because the running process holds its own imported copy of the page code.

**Rule.** Every artefact is reproducible from disk by a second command. If a thing can only be
made by the process that made it, it cannot be restyled, regenerated after a crash, or checked
by anyone else.

**Check: prose only**

---

### L016 — 2026-09-05 — killing a run on this machine, twice

**What happened.** `Get-Process python` does not match the Store Python (`python3.11.exe`), and
`kill -9 $!` under Git Bash kills the subshell and leaves the Python child running. Both times
the result was two runners on one study.

**Rule.** Stop a run by pid, found with
`Get-CimInstance Win32_Process -Filter "Name like '%python%'"` filtered on the command line,
and confirm nothing survived before starting anything else.

**Check: prose only**

---

### L017 — 2026-09-06 — a training-batch statistic read as the registered one

**What happened.** `participation_ratio_norm` off a training batch is capped at
`min(batch-1, latent_dim)/latent_dim` — 0.121 at batch 32 and 256 dimensions. On Study 3 the
batch curve ranked the four variants in the *opposite* order to the held-out value.

**Rule.** The registered number is the held-out one. A training curve is a trend.

**Check: prose only**

---

### L018 — 2026-09-05 — a drift curve that looked fine

**What happened.** A model with a flat drift curve under 1 and an `action_sensitivity_ratio`
near zero is reproducing the mean trajectory. It looks like a good world model on the curve
that is easiest to read, and is useless for a policy.

**Rule.** Read the action metric beside the drift curve, never instead of it.

**Check: prose only**

---

### L019 — 2026-09-05 — momentum scored as the action

**What happened.** On a snapshot collected with held actions, both a classifier and a model can
score by decoding the momentum the previous action built. A model that learns
action-equals-current-velocity overshoots exactly when a policy changes direction.

**Rule.** On sticky data, the hold-start column is the gate, not the overall one.

**Check: prose only**

---

### L020 — 2026-09-06 — the reward was not a proxy for the error it stood in for

**What happened.** Phase 4's planner covered 23 grid cells against random's 325 while earning
2.4x random's ensemble disagreement. It succeeded at its objective. Ensemble disagreement
correlates approximately zero with the one-step error it is used to proxy for, and *negatively*
with that error relative to copy-last — so maximising it seeks transitions the model handles
comparatively well.

**Rule.** Before building on a reward, measure whether it tracks the quantity it stands in for.
A learning-progress reward built on the same signal inherits the same fault, because it is the
same signal differentiated.

**Check: prose only**

---

### L021 — 2026-09-07 — a check that enforced an artefact nothing produced

**What happened.** C09 asks whether the canary passed at a commit no older than the last change
to the machinery. It reads `<ledger>/canary.json`, and so does the state page's Health section.
Nothing in the package ever wrote that file. The check could only ever report "no canary result
on file", in every project, and the page could only ever print "never run".

What kept it hidden was the test. It hand-wrote `canary.json` itself and then asserted the
reader responded to it: the reader was covered, the writer did not exist, and the suite was
green. The neighbouring case was worse still. It wrote commit `000…0` and asserted the check
went quiet — and the check went quiet because `git diff` cannot resolve that commit and a git
error was being read as "nothing changed". Both halves passed for the wrong reason.

**Rule.** A test for a reader must consume what the writer produced. Hand-writing the artefact
under test proves only that the reader parses JSON. And an artefact the tooling enforces but
does not produce has to be declared as absent where a user reads it, rather than left to be
discovered by someone wondering why a check never has anything to say.

**Check: prose only** — `tests/test_artefacts.py` asserts that every artefact kind the layouts
enforce has a producer in the package, and that each deliberate exception is admitted in the
README's "What is not built yet".
