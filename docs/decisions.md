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
