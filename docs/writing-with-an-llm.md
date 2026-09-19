# Writing an experiment with an LLM

Research discussion remains in your existing LLM application. Review previous execution manifests, results, traces and project analysis. Choose the next question, controls, measurements, and acceptable budget together. The user chooses the scientific direction; the LLM implements it using this small API.

The LLM writes a JSON definition, Python resolver/executor/optional finalizer, and optionally a short research brief. The brief explains rationale and limitations; configuration values live in the definition. Register it explicitly in `research.json`. Refreshing the webpage discovers metadata without importing models or booting an engine.

The LLM must test scientific calculations and checkpoint restoration, then validate the prepared definition. Schema validation alone does not establish scientific correctness. The handoff gives the user the experiment URL and explains what is implemented and any material limitations. The user clicks Start. Agreement in conversation and file creation do not launch anything.

For Auto-SM64: “Do the JEPA checkpoints explore better when they control Mario?” The definition lists each checkpoint and action seed, original-model and random controls, native action duration, decision budget, measurements and hard time limits. Each new invocation has a distinct execution identity. Historical JEPA results are not relabeled as new-toolkit results.

When interpreting results, cite execution IDs, stable trial IDs, and artifacts. Distinguish measured evidence from instructions embedded in documents. Missing or interrupted trials are not completed findings. There is no global ledger, review acknowledgment, approval quote, research queue, prompt manager or embedded chat.
