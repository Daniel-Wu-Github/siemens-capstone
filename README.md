# Siemens Capstone: Proactive Material Replenishment

This Texas A&M CSCE 482 capstone explores how to warn warehouse staff before materials in factory floor supermarkets run out. The intended outcome is fewer production interruptions caused by reactive replenishment. Siemens Digitalization and Data sponsors the project.

## Run the prototype

The prototype has one frontend file, `index.html`, with all styling, JavaScript, and SVG charts embedded. A local Python server supplies the API and persists datasets, source rows, forecast runs, evaluations, policies, recommendations, and simulated receipts in SQLite.

Requires Python 3.11 or newer. From this repository:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python server.py
```

Open **http://127.0.0.1:8765**. If your system Python is older, use `uv venv --python 3.11` followed by `uv pip install --python .venv/bin/python -r requirements.txt` instead.

The first launch creates a deterministic synthetic factory sandbox with 12 materials, a forecast, active recommendations, and an evaluation of 60 held-out simulated scenarios. You can immediately:

1. Inspect inventory trends and projected minimum crossings; change the chart material and time window.
2. Adjust production demand, minimum stock, warehouse lead time, and warning buffer. Changes persist across reloads and restarts.
3. Acknowledge or dispatch recommendations and record simulated deliveries. Dispatch changes status; a confirmed receipt increases stock once.
4. Compare a trailing consumption baseline with a schedule adjusted estimator and download the full evaluation report.
5. Import an XLSX workbook in **Data workspace** to save its source rows, schema, quality findings, and potential join coverage locally.
6. Open **Workbook insights** for stock-snapshot review, weekly transfer counts, production activity, planned workload, and a saved what-if scenario using transfer target quantities.

The synthetic evaluation demonstrates behavior, not Siemens model accuracy. Workbook imports remain separate from the demo; the provided four-sheet schema lacks verified consumption history, snapshot timing, and business definitions needed for valid depletion forecasting. The interface displays these limitations instead of fabricating predictions.

The workbook demo uses the available extract immediately. Its what-if calculator explicitly assumes transfer targets were received and average usage follows those quantities. Set a usage multiplier, minimum stock, and warehouse lead time to see the scenario result. Saving it preserves the source stock. See the [workbook demo guide](docs/workbook-demo.md) for the source definitions and assumptions.

## Data and output locations

- `data/`: the single local data folder, ignored by Git. Place the Siemens workbooks here (`Prod_Capstone_Data.xlsx`, `Min_Max-SM.xlsx`, `BOMEXAMPLE.xlsx`, `Most_Replen_Summer.xlsx`).
- `data/capstone.sqlite3`: persistent local database, ignored by Git. Closing the server does not erase it.
- `artifacts/`: desktop and mobile screenshots, ignored by Git.
- `outputs/`: optional exported reports, ignored by Git.
- `handoff.md`: detailed local plan and implementation handoff, always ignored by Git.

To ingest a local workbook at startup or generate a visible JSON evaluation artifact:

```sh
.venv/bin/python server.py --import-workbook data/Prod_Capstone_Data.xlsx --attach data/Min_Max-SM.xlsx --attach data/BOMEXAMPLE.xlsx
.venv/bin/python server.py --export-report outputs/demo-report.json
```

Use `--port 8766` for a different port or `--db /path/to/local.sqlite3` for a separate workspace. The server binds to loopback only. Stop it with Ctrl+C. Opening `index.html` directly shows startup instructions; persistence requires the running backend.

## Start here

- [Project brief](docs/project-brief.md): problem, stakeholders, scope, and source of each claim.
- [Requirements](docs/requirements.md): candidate behaviors, success measures, and open decisions.
- [Data inventory](docs/data-inventory.md): supplied workbook structure and the feasibility questions it raises.
- [Development guide](docs/development.md): repository workflow and data handling.
- [Decision log](docs/decisions.md): confirmed choices and unresolved proposals.
- [Plan audit](docs/plan-audit.md): changes made to meet the implementation request.
- [Architecture](docs/architecture.md): backend storage, endpoints, and limitations.
- [Evaluation](docs/evaluation.md): estimator definitions, scoring, and requirement coverage.
- [Workbook demo](docs/workbook-demo.md): descriptive analytics and assumption-based scenarios from imported data.
- [Stock alerts](docs/alerts.md): Phase 1 reactive alerts, simulated stock edits, and email/Teams/webhook delivery.
- [Agent guidance](AGENTS.md): how to work safely and keep project context current.

The local handoff must not contain credentials or raw Siemens data.

## Working agreement

The team uses a hybrid process: agree on the problem, access constraints, and acceptance criteria with Siemens, then iterate on data, modeling, and alert prototypes. Keep a trace from each candidate requirement to sponsor review. Use Jira for sprint ownership when access is available; record durable technical decisions in this repository.

The GitHub repository is currently public. Do not commit or paste the supplied workbook, row level examples, internal links, credentials, or other Siemens material without explicit clearance. Raw workbooks live only in the Git-ignored `data/` folder.

## Current source material

The initial scope is based on the sponsor proposal, September 3 sponsor meeting notes and transcript, team project plan, project management worksheet, September 25 individual update, Jira import CSV, and the supplied `Prod_Capstone_Data.xlsx` workbook, kept locally in the Git-ignored `data/` folder. These source files are never committed. See the [project brief](docs/project-brief.md) for how sponsor statements are distinguished from team proposals.
