# Research workflows

The board keeps registration, queue membership, holds, execution, review, and research
decisions separate. Queueing or releasing a hold never starts an experiment. Compute
approval never releases a research hold.

## Discovery and board views

Only immediate `*.toml` children of `[paths].specs` are registrations. The configured
queue file is excluded. Discovery does not recurse into archives, scan output folders,
or resurrect registrations from ledger or queue history. Invalid registrations remain
in Specifications with their source and diagnostic. A broken item does not hide other work.

On hold includes every held queue entry, even a missing registration. Release it with
an explanation before removing it. Next work contains explicitly queued, unheld work;
running and completed work are displayed separately. Specifications offers queue membership
controls. Unknown unheld queue targets can be removed without creating a registration.

The JSON state retains `ready` for older clients; it is a specification inventory, not
the operational queue. New clients use `catalog`, `on_hold`, `queued`, and `queue_revision`.
Each run exposes capabilities with `enabled`, `reason_code`, `reason`, and `next_step`.
The historical preview remains read-only.

## Decision policy and acknowledgement

An experiment normally requires a research decision. A measurement normally requires
acknowledgement only. Report checkboxes do not establish a decision requirement.

An explicit registration can override the default:

```toml
[workflow]
decision_required = false
```

Precedence is explicit specification metadata, optional `kind.decision_policy(spec)`,
legacy Reviewed compatibility, then the experiment/measurement default. The optional
hook returns a dictionary with boolean `decision_required`, optional string-list `choices`,
and optional `explanation`; returning `None` defers to the remaining rules.

A legacy report offering only `Reviewed` (ignoring Markdown emphasis, case, whitespace,
and an explanatory dash suffix) requests acknowledgement only. Reviewed is never a
research choice, including in mixed reports. Explicit policy takes precedence. A required
decision with no research choices displays a repair instruction instead of disappearing.
Legacy submissions choosing only Reviewed acknowledge evidence; mixed acknowledgement
and research-choice submissions are rejected.

Mark reviewed stores a timestamp, optional note, locally configured Git name (or Local
user), evidence revision, and source manifest in `reviews.json` under the configured
ledger directory. The actor is attribution, not authentication. Review does not approve
compute, release holds, or resolve a required research decision. A research decision also
acknowledges its evidence, avoiding a second click.

## Evidence and historical results

Evidence revision hashes the specification fingerprint, canonical result content,
declared historical source files, authored Reading text, and resolved decision requirement
and choices. Heartbeats, generated bookkeeping timestamps, JSON formatting, checkbox
selections, and decision notes do not change it. Changes from a recorded evidence revision require another review.
Decision-required evidence also requires reconsideration; reaffirming a previous choice
requires a new reason and creates a new record superseding the previous decision.

Decision ledger rows now include `evidence_revision`, `operation_id`, `binding`, `sources`,
and `actor`. Decision deduplication includes the evidence revision. Other finding identities
are unchanged. Existing rows are never rewritten. Current, Earlier evidence, and Evidence
revision unknown are distinct applicability states. Matching an old fingerprint and commit
does not silently bind an unversioned decision or acknowledgement to current evidence.

Missing legacy metadata alone does not reopen previously handled work. Unversioned
decisions and acknowledgements (including checked Reviewed reports) remain resolved
history labeled **Evidence revision unknown**. This compatibility resolution neither
asserts current evidence coverage nor writes a revision, migrates records, or changes
holds. State exposes `legacy_resolved` separately from evidence applicability.
An acknowledgement alone still does not resolve a required research decision.

If a recorded evidence revision differs from the current revision, that demonstrates
change: unknown legacy history cannot suppress another review or a required research
decision. A new explicit acknowledgement or decision records a real revision normally.
Without a recorded baseline, the toolkit cannot establish a content change retroactively
and does not fabricate one from missing metadata, file timestamps, or report generation.

Standard per-unit results continue using `kind.read_result`. An optional
`kind.historical_evidence(spec, out)` hook supports aggregate-only evidence:

```python
def historical_evidence(self, spec, out):
    return {
        "completion": "complete",  # complete, incomplete, or unknown
        "summary": self.read_historical_summary(out),
        "sources": ["historical-results.json"],
        "explanation": "Completion verified against the historical trial manifest.",
    }
```

Sources must be readable files inside `out`. The hook is used only when standard
execution evidence and a run lock are absent. Root reports or summaries alone mean
historical evidence with unknown completion. Unit counts are not invented. Historical
evidence can be acknowledged while inactive, but a research decision requires verified
completion, a usable summary, and research choices. Reading does not migrate artifacts.

## Mutation API and retry contract

All queue, review, and decision requests require a nonempty `operation_id`. IDs share
one project-wide namespace and bind to the action, run, and complete payload, including
expected revisions. Reuse for a different request returns `409 operation_id_reused`.
Keep the exact original payload when retrying an uncertain response; use a new ID for
a revised request. Committed retries are recovered before stale-revision checks.

| Endpoint | Additional fields |
|---|---|
| `POST /api/queue/add` | `run`, `revision` from state |
| `POST /api/queue/remove` | `run`, `revision`; target must be unheld |
| `POST /api/queue/release` | `run`, `revision`, nonempty `note` |
| `POST /api/review` | `run`, `evidence_revision`, optional `note` |
| `POST /api/decide` | `run`, `evidence_revision`, `choices` or zero-based `selected`, nonempty `note` |

Queue responses include the current byte revision. Evidence receipts identify the
original committed revision and separately report its current applicability. A queue
change and its history event are one atomic TOML write; comments and unknown fields are
preserved. Review records and their operation receipts are one atomic JSON write. The
operation journal recovers interrupted receipts from authoritative records. A partially
saved decision is acknowledged on retry against its original revision, never newer results.

The CLI requires an explicit revision for research decisions:

```text
python -m rl_researcher.decide RUN --evidence-revision REVISION --note "Reason" --operation-id REQUEST_ID
```

Read the revision from `/api/run/RUN`. The CLI reads selected choices from the authored
report. A legacy Reviewed-only invocation is an acknowledgement of the current snapshot.

OS-backed project locks serialize toolkit edits, review/decision commits, and execution
startup across processes; the lock order is workflow before ledger. Launches reserve their
state before spawning, and review is refused while a launch or execution is active. A lock
timeout never steals a live lock. CLI hold enforcement is independent of `--no-gate`,
`--no-check`, and guard overrides, and returns refusal exit code 4.

External editors do not participate in toolkit locks. Changes detected before commitment
return a conflict. A record committed before an external edit still names only the older
revision and is displayed as stale. Conflicts preserve browser drafts and never resubmit
automatically.

Transport failures, HTTP failures, malformed responses, and rendering failures have
different messages. Resource errors clear only when that resource refreshes successfully;
the last successful board remains visible with its refresh time.

## Validation

```text
pip install -e ".[dev,browser]"
python -m playwright install chromium
pytest -q
```

Set `RL_BROWSER_TESTS=1` to run `pytest tests/test_browser_workflows.py -q` against real
HTTP and Chromium. CI runs this suite on Windows and Linux. All fixtures are disposable;
no adjacent project registrations, archives, results, or queues are changed.

`workflow.lock` and `launches.json` are local coordination files; exclude them from
version control in consuming projects. Queue history, reviews, decision rows, and operation
receipts retain the audit trail.
