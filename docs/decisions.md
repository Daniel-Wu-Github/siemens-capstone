# Decision log

Use this file for decisions that change scope, data contracts, architecture, or success criteria. Record the date, decision maker, evidence, and consequences. A draft in the handoff or Jira is not a sponsor decision.

| Date | Topic | Status | Evidence and next step |
| --- | --- | --- | --- |
| 2026-09-29 | Prototype focus | Working plan | Sponsor proposal and meeting prioritize depletion prediction and actionable replenishment alerts. Confirm first release acceptance criteria. |
| 2026-09-29 | SCADA signals | Deferred proposal | Team checkpoint plan defers direct SCADA integration. Confirm with sponsor if this remains the boundary. |
| 2026-09-29 | Azure and security | Open | Team plan proposes Azure only; sponsor meeting described Microsoft as familiar and requires cybersecurity classification for candidate tools. Seek explicit platform and data handling approval. |
| 2026-09-29 | Dashboard channel | Open | Sponsor uses Tableau but described the visualization and notification design as flexible. Choose after an alert review. |
| 2026-09-29 | Raw workbook in Git | Do not add | GitHub repo is public; sharing permission for the workbook is unconfirmed. Keep extracts outside version control. |
| 2026-09-29 | Jira connectivity | Open | Atlassian agent connection is available but not connected. Jira site, board key, and GitHub integration state are unknown. |
| 2026-09-29 | Local implementation | Authorized and implemented | User requested the entire local prototype with TDD, a single HTML frontend, and backend persistence. Python, SQLite, and embedded HTML/CSS/JS meet that request. |
| 2026-09-29 | Demonstration and evaluation data | Implemented | Deterministic synthetic material histories and 60 held-out scenarios provide immediate, visibly labeled output. Imported workbooks cannot inherit synthetic predictions or scores. |
| 2026-09-29 | Candidate estimator | Implemented prototype | Schedule adjusted units per load estimated from prior observations is compared with a trailing average. This is an interpretable estimator; production model selection awaits valid labels. |
| 2026-09-29 | Replenishment workflow | Implemented simulation | Acknowledgement and dispatch persist status. A receipt adds stock once in a database transaction; remaining risk can open a new recommendation. |
| 2026-09-29 | Available-workbook demonstration | Implemented | User requested an immediate demo using the supplied extract. Descriptive stock, transfers, production, and schedule views run locally; a separate saved what-if exposes assumptions about transfer target quantities, units, and usage. |
| 2026-10-08 | Sponsor reference files | Implemented (team) | Sponsor supplied Min/Max tolerances, an example BOM, and a production-status code table. The workbook view now uses them for bin health, BOM-implied demand, and days to minimum. Values remain estimates until daily stock history exists. |
| 2026-10-08 | Replenishment definition | Team proposal; confirm with sponsor | The transfer log includes returns and supermarket-to-supermarket moves. Rates count only warehouse/VLM (`001`/`011`) to supermarket (`4xx`) transfers. |
| 2026-10-08 | Warning horizon replaces warehouse lead time | Team proposal; confirm with sponsor | No measurable delivery time exists in the data. The UI uses a configurable horizon (default one 8-hour shift). |
| 2026-10-09 | Minimum 0 is a real minimum | User decision | An empty bin with min 0 alerts. |
| 2026-10-09 | One alert definition across pages | User decision | The Stock alerts definitions are authoritative; Workbook insights now count the same 72 at-or-below positions and use the same tolerance flags. |
| 2026-10-09 | Every alert change is notified | Implemented after independent QC | Page loads, reference imports, edits, and resets all notify the changes they cause, once. Open sponsor questions are in `SPONSOR_QUESTIONS.md`. |
| 2026-10-09 | Lead time 3 h for stocked and fab (user decision; sponsor to confirm fab value) | User decision | Both lead times default to 3 h and are configurable. Sponsor asked for two values (stocked vs built in fab); the fab value, safety buffer, and divergence threshold are open sponsor questions. |
| 2026-10-29 | App reduced to Stock alerts, Predicted alerts, Replenishment insights, plus Data sources and Settings; all other pages, the synthetic sandbox and their API routes retired to `legacy_for_deletion/` | User decision (Daniel Wu) | Frontend redesigned to `docs/frontend_design_system.md` (accepted). Single active workbook (most recent import). Part forecast what-if controls dropped; the calculation steps live in the Predicted alerts detail. Replenishment insights is a labelled placeholder until its backend is planned. Mobile layout not required. See `legacy_for_deletion/MANIFEST.md`. |
| 2026-10-29 | Phase 2 formulas locked in `docs/formulas.md` | User decision (Daniel Wu) | Decided rule: demand blend history 1 : schedule 1 (observed 0), hours to minimum, Alert / Predicted (lead + 24 h) / Watch (72 h) tiers. Code still has safety 0, observed weight 1 and no Watch tier until the Phase 2 backend update. Not sponsor confirmed. |
