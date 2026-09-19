# RL-Researcher

A local webpage for operating and observing experiments: one sequential execution per project, fixed budgets, durable checkpoints, and evidence saved per execution. The runtime uses only Python's standard library.

Double-click **Open Research** in Auto-SM64. Inspect a prepared definition, validate it, and click **Start experiment**. Stop, resume, inspect live behavior and open saved artifacts through the same application. Research discussion and scientific interpretation remain with the user and their LLM.

- [Using the webpage](docs/using-the-webpage.md)
- [Writing an experiment with an LLM](docs/writing-with-an-llm.md)
- [Integration API](docs/integration-api.md)
- [Live evidence and recovery](docs/live-evidence-and-recovery.md)
- [Legacy access and migration](docs/legacy-and-migration.md)
- [Deterministic launchable example](examples/deterministic/definition.json)

There is no public research-command collection, global findings ledger, scientific review queue, approval workflow, autonomous planner, scheduler or report generator. A prepared definition never starts itself.

Auto-SM64's JEPA closed-loop comparison is the first migrated customer. Historical evidence stays in place; frozen legacy source and verified inventories are outside the active package in the sibling `research-reduction/baseline` directory.
