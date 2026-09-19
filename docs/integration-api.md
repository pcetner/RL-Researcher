# Integration API

The deterministic example under `examples/deterministic` is a complete launchable project. Runtime dependencies are Python's standard library. Scientific dependencies belong to the project and are declared by distribution name in the definition.

`research.json` contains `experiments: [{id, definition}]` and optional `history: [{label, path}]`. Definitions contain `name`, `question`, `setup`, entrypoint references, `sources`, `python_paths`, `inputs`, `dependencies`, `limits.overall_seconds`, ordered `trials`, and optional fixed display metadata. Entrypoints use `project/relative/file.py:function`. Trial IDs must be path-safe and unique. Each trial declares a readable label and positive time limit in seconds; domain fields are project-owned.

The resolver `resolve(definition)` validates project configuration and returns the displayed definition. Keep it metadata-only; it runs in a separate validation process with a 30-second timeout. Derived trial configurations should be written into the definition document so the user sees exactly what will run. Entrypoints and their transitive local sources must be declared. No model loading or engine startup belongs in the resolver.

The executor `execute(ctx, trial, checkpoint)` returns a JSON-serializable result. `checkpoint` is None for a new trial, otherwise a validated immutable payload directory. Never infer recovery from mutable work files. `ctx.work` is a trial working directory; `ctx.inputs` maps names to verified original input paths. `ctx.source` is captured project source; `ctx.definition` and `ctx.manifest` are frozen configuration and provenance.

Context operations:

| Operation | Responsibility |
|---|---|
| `ctx.phase(label)` | Publish meaningful phase changes. |
| `ctx.progress(decision)` | Publish work coordinates when useful. |
| `ctx.sample(decision, metrics, preview, details, force=False)` | Publish one coherent trial/attempt sample; ordinarily at most once per five seconds. |
| `ctx.message(text, level='info')` | Record a structured diagnostic message. |
| `ctx.publish_artifact(path, label, media_type)` | Publish immutable content-addressed bytes from inside the execution. |
| `ctx.publish_checkpoint(path, decision)` | Publish a file or directory from this trial's work directory, returning identity and hashes. |
| `ctx.check()` | Raise cooperative stop or fixed-budget exceptions; call between transitions and during long loops. |
| `ctx.stop_requested` | Read whether a safe stop has been requested. |

Every metric is `{value, count, kind, reason?}`. Use null plus a reason for unavailable values. Display metadata contains up to three metric definitions with IDs, labels, definitions, digit counts and optional percentage formatting. Trials can declare up to two stable control trial IDs. Project validation must ensure those controls match the relevant seed. The viewer truncates references to the selected trial's decision coordinate.

Save recoverable domain state in a `finally` block when cooperative stop or budget exceptions unwind. Include all RNG, observation memory and domain state required for exact continuation. The framework only validates publication and identity; it cannot establish semantic replay correctness.

The optional `finalize(ctx, results)` consumes completed trial results and publishes project-authored analysis. It is a visible phase. A failed finalizer can be resumed without rerunning completed trials. No framework winner or scientific verdict is generated.
