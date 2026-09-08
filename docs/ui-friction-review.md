# UI friction review

Implemented after the second review of the Auto-SM64 reference preview.

| Complaint | Root cause and other affected areas | Change |
|---|---|---|
| Refresh is confusing | Live polling already ran every five seconds, but the toolbar did not explain it. The historical preview served fixed data. A Home focus guard could indefinitely defer visible updates. | Live boards say “Live · 5s”; reconnect appears only after a connection failure. The saved-data view has one compact Preview label. Home updates preserve focus and expanded context. |
| Home looks out of place | Home reused the prominent run-selection button style. | Project name links home; the sidebar uses a quiet Overview link. |
| Sidebar truncates names and hides context | Fixed 250px width, ellipses, and separate stacked name/status blocks. | Wider responsive sidebar with aligned Run / Needs / Waiting columns. Long names wrap within their column. Search is retained. |
| Waiting ages are missing | The live state had completion dates, but the preview dropped them. Queue records do not necessarily contain a hold/start timestamp. | Shared relative formatting across sidebar, attention rows, findings, and decisions. Missing timestamps remain unknown. Failed/stopped work uses its recorded progress timestamp. Queue entries can provide `since`. |
| Markdown opens another tab | One shared link helper forced every file into a new tab; nested report links also lacked project-relative resolution. | Plan, Full Report, specifications, and linked documents open in a right-hand dialog. Markdown is rendered, code files remain readable, and relative links are resolved inside the project. Traversal outside the project is rejected. |
| Happening now is not a page | It was only a Home section. Navigation also ignored requested run tabs on initial load. | Added the bookmarkable `#activity` view and fixed run/tab route restoration. |
| Hypothesis is too long and poorly formatted | Overview copied registration prose wholesale, with a narrow inline formatter and preserved source line breaks. | A one-sentence question precedes the registered outcome. A short hypothesis follows it. Editorial summaries live outside the registration; original documents remain accessible. |
| Registered outcome loses clarity | `plain()` removed Markdown emphasis/code before HTML escaping, leaving identifiers embedded in a long sentence. This also affected Results and some other summaries. | Shared safe inline formatting, with arm/metric identifiers rendered as code in outcomes. Metric tables and figures remain intact. |
| Findings sound like generated prose | Home reused the first report paragraph, including some unwritten “write here” placeholders. | Reject placeholders; support concise editorial finding sentences. The Auto-SM64 preview uses source-grounded summaries. |
| Decision controls are buried | The form belonged to the Overview body and disappeared on other tabs. Its generic multi-selection UI exposed checkboxes. The API substituted the choice text for an empty reason. | Sticky decision header across run views, one button per offered choice, required reason in both browser and API. Custom choices remain supported. The preview demonstrates validation without writing a decision. |
| Units is unexplained | A framework storage term was used as navigation copy. | Renamed to Run breakdown and explained as variant/seed trials, used to inspect progress, failures, and recovery. Related navigation labels were updated. |

Validation: 554 tests passed; lint, type checks, JavaScript syntax, and wheel build passed.
Browser checks covered the drawer, activity routing, relative ages, required reasons, persistence
across tabs, Markdown code formatting, and mobile layout. The original Auto-SM64 checkout was
not changed. The preview continues to reject writes to research artifacts.

Editorial display examples are in `docs/examples/autosm64-ui.json`. Copy to a project's
`research-ui.json` to use them. This file changes presentation only, not specifications,
registered metrics, fingerprints, results, or recorded decisions.
