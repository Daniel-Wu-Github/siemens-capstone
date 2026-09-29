# Development workflow

## Current state

The repository contains a working local prototype authorized by the user. Python 3.11 serves one HTML frontend and persists state in SQLite. `openpyxl` is the only runtime dependency. The alert channel is an in-app recommendation queue; actions simulate dispatch and receipt. Azure services and production delivery channels remain undecided.

See the README for setup and test commands. Core and HTTP contracts were exercised with failing tests before their implementations. Regression tests cover time leakage, stale data, threshold calculations, deduplication, concurrent deliveries, workbook contracts, restart persistence, and browser workflows. All tests use synthetic data and temporary databases.

## Proposed working loop

1. Select a Jira issue after checking the live board and its acceptance criteria. Link the issue to the relevant requirement ID in `docs/requirements.md`.
2. Make a focused change that can run locally on approved data or synthetic fixtures. Keep input paths in ignored local configuration rather than hard coding them.
3. Add validation for the behavior that matters: joins and data quality, historical leakage, baseline comparison, or alert timing. Document a limitation if the data cannot support a feature.
4. Ask for a peer review. Record the verification performed and update documentation when assumptions or contracts change.
5. Demonstrate sponsor facing outcomes with approved examples; capture feedback as a decision or requirement change.

## Git and Jira

The repository has an `origin` remote, but GitHub and Jira are not confirmed to be integrated. Once the Jira site and GitHub repository are connected by authorized administrators, use the actual Jira issue key in branch names, commit messages, and pull request titles so development activity appears on the work item. Never invent keys from the CSV backlog. Direct agent reading or editing of Jira requires a separately connected Atlassian integration and suitable account permissions.

## Data and release review

Before sharing a commit, pull request, artifact, notebook, screenshot, or model, check that it contains no raw Siemens records, secrets, internal links, or inferred sensitive operational metrics. A local demo is distinct from production deployment; Siemens must approve the destination, data handling, security classification, and users before a live connection or deployment.
