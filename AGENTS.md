# Agent working guide

Read `README.md`, `docs/project-brief.md`, `docs/requirements.md`, `docs/data-inventory.md`, and `docs/decisions.md` before changing implementation or scope. Read local `handoff.md` when available. It is ignored by Git and is not a substitute for committed project documentation.

- The repository is public. Do not commit raw Siemens data, sample rows, confidential documents, credentials, tokens, or internal URLs. Keep Siemens files only in the Git-ignored `data/` folder at the repository root unless Siemens explicitly approves a publishable fixture.
- Identify whether a statement is sponsor confirmed, team proposed, or still an assumption. Record decisions with date, decision maker, and evidence in `docs/decisions.md`.
- Prefer a reproducible offline prototype from approved, anonymized extracts before proposing live integrations or deployment.
- Do not claim model accuracy, stockout prevention, security compliance, or operational readiness without corresponding evidence and sponsor review.
- Jira is a coordination tool, not the sole source of technical truth. If Jira access becomes available, read the current board before selecting work; use issue keys in branches, commits, and pull requests after the Jira and GitHub integration is confirmed. Do not move or edit unrelated issues.
- Update the README and relevant docs when implementation changes a documented interface, data contract, or requirement.
- UI work: read `docs/frontend_design_system.md` first (tokens, layout, components). Do not change the visual design without the user's approval.
- The Phase 2 formulas in `docs/formulas.md` (also summarized in the README and implemented in `proactive.py`) are locked. Never change them, their defaults or their wording without the user's explicit permission for that specific change.
