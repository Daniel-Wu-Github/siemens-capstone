# Siemens Capstone: Proactive Material Replenishment

This Texas A&M CSCE 482 capstone explores how to warn warehouse staff before materials in factory floor supermarkets run out. The intended outcome is fewer production interruptions caused by reactive replenishment. Siemens Digitalization and Data sponsors the project.

## Run the prototype

The prototype has one frontend file, `index.html`, with all styling, JavaScript, and SVG charts embedded. A local Python server supplies the API and persists imported workbooks, reference files, stock alerts, settings, and notifications in SQLite.

Requires Python 3.11 or newer. From this repository:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python server.py
```

Open **http://127.0.0.1:8765**. If your system Python is older, use `uv venv --python 3.11` followed by `uv pip install --python .venv/bin/python -r requirements.txt` instead.

The app has three pages and two setup pages. **Stock alerts** (Phase 1) lists bins at or below their minimum and sends alerts. **Predicted alerts** (Phase 2) forecasts which bins will reach their minimum within the lead time plus a safety buffer, and shows how each number was calculated. **Replenishment insights** (View 2) is a planned page and shows no data yet. **Data sources** imports the workbook and reference files (Min/Max tolerances, BOM, dated bin-status snapshots). **Settings** holds the forecast settings. The design is described in [docs/frontend_design_system.md](docs/frontend_design_system.md).

On first launch the workspace is empty: open **Data sources**, import the workbook, then attach the reference files. The earlier synthetic sandbox, overview, materials, model evaluation, workbook insights and part forecast pages were retired; their code is kept for review in `legacy_for_deletion/` (see its `MANIFEST.md`) and is not run by the app or the tests.

The forecast is an estimate from recorded transfer quantities, not measured consumption. It has not been validated against real stockouts.

## Data and output locations

- `data/`: the single local data folder, ignored by Git. Place the Siemens workbooks here (`Prod_Capstone_Data.xlsx`, `Min_Max-SM.xlsx`, `BOMEXAMPLE.xlsx`, `Most_Replen_Summer.xlsx`); the real-file tests read them from this folder and skip when they are absent.
- `data/capstone.sqlite3`: persistent local database, ignored by Git. Closing the server does not erase it.
- `artifacts/`: screenshots produced by browser tests, ignored by Git.
- `handoff.md`: detailed local plan and implementation handoff, always ignored by Git.

To ingest a local workbook at startup:

```sh
.venv/bin/python server.py --import-workbook data/Prod_Capstone_Data.xlsx --attach data/Min_Max-SM.xlsx --attach data/BOMEXAMPLE.xlsx
```

Use `--port 8766` for a different port or `--db /path/to/local.sqlite3` for a separate workspace. The server binds to loopback only. Stop it with Ctrl+C. Opening `index.html` directly shows startup instructions; persistence requires the running backend.

## Verification

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest -q
```

To run only the backend tests, use `pytest -m 'not browser'`. Browser tests use temporary databases and synthetic workbooks, exercise saved edits, import, and desktop layout, and capture screenshots in `artifacts/`. See [architecture](docs/architecture.md) for API contracts.

Live SCADA ingestion, factory system writes, real notifications, cloud deployment, and sponsor UAT remain outside this local implementation. Siemens approval and missing source definitions are still needed for operational use.

## Start here

- [Project brief](docs/project-brief.md): problem, stakeholders, scope, and source of each claim.
- [Requirements](docs/requirements.md): candidate behaviors, success measures, and open decisions.
- [Data inventory](docs/data-inventory.md): supplied workbook structure and the feasibility questions it raises.
- [Development guide](docs/development.md): repository workflow and data handling.
- [Decision log](docs/decisions.md): confirmed choices and unresolved proposals.
- [Plan audit](docs/plan-audit.md): changes made to meet the implementation request.
- [Architecture](docs/architecture.md): backend storage, endpoints, and limitations.
- [Stock alerts](docs/alerts.md): Phase 1 reactive and Phase 2 predicted alerts, simulated stock edits, and email/Teams/webhook delivery.
- [Phase 2 formulas](docs/formulas.md): locked demand, hours-to-minimum, alert-tier and suggested-quantity formulas.
- [Agent guidance](AGENTS.md): how to work safely and keep project context current.

The local handoff must not contain credentials or raw Siemens data.

## Phase 2 formulas (locked)

Full definitions and a worked example: [docs/formulas.md](docs/formulas.md). Changing any of them needs the user's explicit permission each time.

- `demand/day = (1 x history + 1 x schedule) / 2`; if schedule differs from history by more than 50%, use schedule only.
- `hours_to_min = max(0, available - min) / demand x 24`
- Alert if `available <= min`; Predicted if `hours_to_min <= lead + 24`; Watch (dashboard only) if `hours_to_min <= 72`.
- `suggested_qty = ceil(max - (available - demand x lead / 24))`

## Working agreement

The team uses a hybrid process: agree on the problem, access constraints, and acceptance criteria with Siemens, then iterate on data, modeling, and alert prototypes. Keep a trace from each candidate requirement to a test and sponsor review. Use Jira for sprint ownership when access is available; record durable technical decisions in this repository.

The GitHub repository is currently public. Do not commit or paste the supplied workbook, row level examples, internal links, credentials, or other Siemens material without explicit clearance. Raw workbooks live only in the Git-ignored `data/` folder.

## Current source material

The initial scope is based on the sponsor proposal, September 3 sponsor meeting notes and transcript, team project plan, project management worksheet, September 25 individual update, Jira import CSV, and the supplied `Prod_Capstone_Data.xlsx` workbook, kept locally in the Git-ignored `data/` folder. These source files are never committed. See the [project brief](docs/project-brief.md) for how sponsor statements are distinguished from team proposals.
