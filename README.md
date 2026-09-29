# Siemens Capstone: Proactive Material Replenishment

This Texas A&M CSCE 482 capstone explores how to warn warehouse staff before materials in factory floor supermarkets run out. Siemens Digitalization and Data sponsors the project.

## Project status

This repository establishes the project documentation and local development conventions. The initial prototype will prepare manufacturing extracts, evaluate material risk, and demonstrate actionable replenishment recommendations.

## Start here

- [Project brief](docs/project-brief.md): problem, stakeholders, scope, and source evidence.
- [Requirements](docs/requirements.md): candidate outcomes and acceptance criteria.
- [Data inventory](docs/data-inventory.md): supplied workbook structure and feasibility questions.
- [Development workflow](docs/development.md): team conventions and data handling.
- [Decision log](docs/decisions.md): confirmed choices and open proposals.
- [Agent guidance](AGENTS.md): repository context and working rules.

The detailed local plan is in `handoff.md`, which is always ignored by Git. Raw Siemens data, credentials, generated outputs, and local environments are also excluded from version control. The supplied workbook remains outside this public repository.

## Initial scope

1. Validate and reconcile available production, schedule, replenishment, and bin data.
2. Establish a transparent baseline and evaluate whether the data support depletion forecasting.
3. Produce prioritized, explainable recommendations for warehouse review.
4. Demonstrate results with clear assumptions and limitations.

Live factory system writes, SCADA ingestion, and cloud deployment require separate integration decisions. Technical choices in the team plan remain proposals unless supported by sponsor evidence.
