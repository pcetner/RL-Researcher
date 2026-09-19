# Using the webpage

Double-click **Open Research** in the project directory. It opens the local application; reopening the launcher reuses the existing server. The browser and server may close while an execution continues.

An experiment is a prepared definition. An execution is one invocation with its own identity and directory. The list shows running and interrupted executions first, then completed executions and available definitions. Historical reports are separate and have no active controls.

Open a definition and inspect its question, setup, exact configuration, declared inputs, and fixed limits. **Validate configuration** checks technical readiness. **Start experiment** freezes the revision displayed and creates a new execution. A changed revision is refused; inspect and validate it again. Writing or registering a definition never starts it.

The execution page orders question/setup, current activity, live evidence, all trial results, artifacts, and technical details. Select a trial to inspect its measurements and preview history. Expanded sections, selection and scroll survive polling and page reloads.

**Stop safely** requests checkpointing and shutdown. **Stopping** persists until the worker exits. After 60 seconds without exit, **Force stop owned processes** becomes available; uncommitted progress may be lost. **Resume** explains incompatibilities and restores the same execution using captured source and the original deadlines. Completed trials are skipped. **Start another execution** returns to the definition and preserves all previous executions.

Ready means technically launchable. Running includes preparation and finalization. Completed means every trial and finalization succeeded. Incomplete means a fixed budget prevented completion. Failed means an error or forced termination ended execution. Stopped means the worker exited after a cooperative stop. Unresponsive is a separate missing-update warning, not a scientific verdict.

Artifacts open in the browser or can be saved using the browser's download controls. HTML artifacts are served as text; historical HTML is displayed in an empty sandbox. Complete logs and the journal remain available under technical details.
