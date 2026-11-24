# Siemens Capstone: Proactive Material Replenishment

Texas A&M CSCE 482 capstone for Siemens Digitalization and Data. The prototype warns warehouse staff before supermarket bins run out: it flags bins already below minimum, predicts which bins will get there soon, and shows which parts have replenishment problems. It runs on one computer, in a browser, from one database file.

## Which document do I read first?

| I want to… | Read this | Time |
| --- | --- | --- |
| Understand what this is and see it without any setup | This README, then open the demo (`python scripts/build_demo_html.py`, see below) | 5 min |
| Use the screens | [User guide](docs/USER_GUIDE.md) | 10 min |
| Install and start it | [Install guide](docs/INSTALL_GUIDE.md) | 10 min |
| Load Siemens files or see which columns are needed | [Data import guide](docs/DATA_IMPORT_GUIDE.md) | 15 min |
| Connect a live data source | [Live data integration](docs/LIVE_DATA_INTEGRATION.md), after the Data import guide | 15 min |
| Turn on email, Teams or webhook alerts | [Notification guide](docs/NOTIFICATION_GUIDE.md) (Teams steps: [teams-setup.md](docs/teams-setup.md), draft) | 10 min |
| Check how the numbers are calculated | [formulas.md](docs/formulas.md) (locked), then [data-inventory.md](docs/data-inventory.md) | 10 min |
| Continue development | Repository layout below, then [architecture.md](docs/architecture.md), [alerts.md](docs/alerts.md) (API), [frontend_design_system.md](docs/frontend_design_system.md), then the tests | 1 hour |

Not sure where to start? Read **What this project does**, then **Known limitations**, then the User guide.

## What this project does

Warehouse supermarket bins hold small parts for production lines. When a bin runs below its minimum, the line can stall. This prototype reads Siemens exports (bin status, Min/Max, bill of materials, replenishment transfers, planned production) and shows three things:

- **Stock alerts:** bins already at or below minimum, with how long each alert has been open and when the bin was last replenished. Alerts can be acknowledged and a digest can be sent (saved as a file by default).
- **Predicted alerts:** bins forecast to reach their minimum within lead time plus a safety buffer, with a countdown chart per bin. The forecast is a straight-line estimate from a daily demand rate: `demand = (2 × history + 1 × schedule) / 3`. The formulas are documented in [`docs/formulas.md`](docs/formulas.md) and are locked.
- **Replenishment insights:** parts whose refills are short, frequent or divergent, with CSV export.

It is a local, single-user prototype: one Python process, one SQLite file, one HTML page. It is not a production system and it is not connected to any Siemens system.

## Repository layout

| Path | Purpose |
| --- | --- |
| `index.html` | The whole user interface (HTML, CSS, JavaScript, SVG charts; no build step, no external assets) |
| `server.py` | Starts the local HTTP server (loopback only) and can import workbooks from the command line |
| `src/siemens_capstone/` | Backend: `api.py` (HTTP routes), `store.py` (SQLite persistence, alert lifecycle), `proactive.py` (the locked forecast formulas), `alerts.py` (alert rules and delivery), `ingest.py` (workbook validation), `analytics.py` (replenishment rates and summaries), `insights_parts.py` (refill flags, CSV), `backtest.py` (offline demand-rate check) |
| `tests/` | pytest suite: unit, API, store and real-browser (Playwright) tests |
| `scripts/` | `build_demo_html.py` (single-file demo with invented data), `build_uat_zip.py`, `backtest_rates.py`, `backtest_windows.py`; `scripts/demo/` holds the demo's in-browser backend and mock data |
| `docs/` | `architecture.md`, `formulas.md`, `alerts.md`, `data-inventory.md`, `frontend_design_system.md`, `teams-setup.md` |
| `run.sh`, `run.bat` | One-step start scripts for Mac/Linux and Windows |
| `data/`, `outputs/` | Git-ignored: Siemens data, databases and generated reports. Never committed |

## Requirements

- Python 3.11 or newer.
- Runtime: `openpyxl` (`requirements.txt`). Tests add `pytest` and `playwright` with Chromium (`requirements-dev.txt`).
- No internet access is needed after installing packages. Nothing is emailed unless SMTP, Teams or webhook variables are set (see below).

## Running it from this repository

```
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
.venv/bin/python server.py                       # opens on http://127.0.0.1:8765
```

The repository contains **no Siemens data**, so a fresh start has an empty database (`data/capstone.sqlite3`). Either:

1. open **Data sources** in the app and import the Siemens workbook and reference files, or
2. try it with invented data, no setup: `pip install -r requirements-dev.txt`, then `python scripts/build_demo_html.py` and open `outputs/Siemens_Replenishment_Demo.html` in a browser.

To run the tests: `python -m playwright install chromium`, then `python -m pytest -q` (add `-m 'not browser'` to skip the browser tests). The final code passes all 517 tests.

## Known limitations

- **The forecast is not validated against real stockouts.** The data has no reliable historical bin-status timeline, so only the daily demand rate was checked on held-out transfers (`src/siemens_capstone/backtest.py`). Do not read "Empty in N h" as a measured prediction.
- Demand comes from recorded replenishment transfer quantities and planned production, not from measured consumption.
- The projection is a straight line (average pace). The team compared exponential and recency-weighted variants and flat averages did better on the available data; queuing models are future work.
- The time axis is hours from now, because the snapshot has no time of day.
- **Open since** is when this tool detected a breach, not when stock actually fell, and it restarts if the database is re-imported.
- The default blend (history 2 : schedule 1) and the 3 h lead time are team proposals awaiting sponsor confirmation.
- Single user, no login, loopback only. Do not expose the server to a network.
- Tested on macOS with Chromium. Windows and other browsers were not tested.
- Open questions for the sponsor: whether the transfer export records who confirmed a transfer, the timezone of confirmation times, and the intended meaning of "open since".

## Credits

- **Sponsor:** Siemens Digitalization and Data supplied the problem, data and feedback. Siemens data is confidential and is not in this repository.
- **Team:** Texas A&M CSCE 482 capstone team. Repository collaborators: Daniel-Wu-Github, aryanvvemuri, amasud7, khhny. Earlier checkpoint work by aryanvvemuri (an ML depletion-forecasting pipeline and proactive alert engine) is preserved in the tags `code-check1`, `code-checkmvp`, `unit-testing-checkpoint` and `integration-testing-checkpoint`, and on the `ml-depletion-forecasting` branch. It is not part of the final code.
- **Third-party software:** [openpyxl](https://openpyxl.readthedocs.io/) (MIT), [pytest](https://pytest.org/) (MIT), [Playwright for Python](https://playwright.dev/python/) (Apache-2.0). The interface uses no external fonts, libraries or images.

## Quick start

Two ways to start. **Option A is the one to use** when you have the handoff zip; Option B is for rebuilding the database from the Siemens files.

### Option A (preferred): start from the handoff database

You need Python 3.11 or newer.

1. Unzip `siemens-capstone-handoff.zip`.
2. Open a terminal in the unzipped folder.
3. Run `sh run.sh siemens_capstone.sqlite3` (Windows: `run.bat siemens_capstone.sqlite3`).
4. Open http://127.0.0.1:8765.

The zip's database already holds the workbook, the Min/Max file, the BOM and the alert history, so there is nothing to import. The first start creates a private `.venv` folder and installs one package (`openpyxl`), which needs internet access or an internal pip mirror. Keep the black window open while testing; close it, or press Ctrl+C, to stop. Your edits are kept in `siemens_capstone.sqlite3`.

### Option B: rebuild the database from the Siemens files

Use this to see the import steps, or to start from the raw files. You need Python 3.11 or newer and the Siemens files: the manufacturing workbook (`Prod_Capstone_Data.xlsx`), the Min/Max file and the BOM. Put them in `data/`. The daily bin-status files are optional.

1. Import and build the database (creates `data/new.sqlite3`):
   ```
   python server.py --db data/new.sqlite3 --import-workbook data/Prod_Capstone_Data.xlsx \
       --attach data/Min_Max-SM.xlsx --attach data/BOMEXAMPLE.xlsx --quiet --no-serve
   ```
2. Start the app: `python server.py --db data/new.sqlite3`, then open http://127.0.0.1:8765.

Full steps, optional daily files and the browser upload route: [docs/INSTALL_GUIDE.md](docs/INSTALL_GUIDE.md) (section 4) and [docs/DATA_IMPORT_GUIDE.md](docs/DATA_IMPORT_GUIDE.md).

**Options** (both scripts take the same two optional arguments):

```
run.bat                                   database siemens_capstone.sqlite3 in this folder, port 8765
run.bat my_copy.sqlite3                   a different database file
run.bat siemens_capstone.sqlite3 8766     a different port
```

If a script cannot be used, run it by hand after `python -m venv .venv` and `pip install -r requirements.txt` (use `.venv\Scripts\python` on Windows, `.venv/bin/python` elsewhere): `python server.py --db siemens_capstone.sqlite3`.

**Five-minute tour** (left menu)

1. **Stock alerts:** every bin with its stock, minimum and maximum. Click a column heading to sort, use the search box and filter. Type a lower number in *Available* and press Enter (or *Set*) to simulate a breach: an alert opens and an email is saved as a file in `outputs/outbox` (nothing is emailed). *Reset* on a row, or *Reset simulated stock*, puts it back. *Acknowledge all below-minimum* marks the open below-minimum alerts as seen without changing stock. Alert rows carry a red marker on the left, and the menu shows a red count of open below-minimum alerts you have not acknowledged. **Open since** shows how long each alert has been open (longest first by default; click the heading to cycle) and **Last replenished** shows the date of the most recent recorded refill. Open since is when this tool detected the breach, not when stock actually fell. **Enlarge** on *Last notification* opens the stock alert digest in a larger window (Esc closes it).
2. **Predicted alerts:** bins forecast to reach their minimum within lead time + safety buffer (Predicted), and within 72 hours (Watch). Click a row to see how it was calculated and a chart counting down to the minimum (hollow dot) and to empty (solid dot); the teal dot is now. The 30 d / 45 d / 60 d buttons preview a different history window. Positions with no demand history show why they have no rate.
3. **Replenishment insights:** parts with replenishment problems. Click the flag buttons (short refills, frequent refills, divergent) to filter; change the thresholds live; export CSV.
4. **Data sources:** import the Siemens workbook and reference files (Min/Max, BOM, daily bin status named `Bin_Status_YYYY-MM-DD.xlsx`). Already loaded in the supplied database.
5. **Settings:** lead times, safety buffer, watch window and the **history share** slider (schedule gets the rest). The formulas and the tier counters update live before you save. Presets can be saved and applied.

## How the numbers work

- Demand per day = history share × history rate + schedule share × schedule rate. Default is history 2 : schedule 1 with the "differs by more than X %" override switched off (Settings). The history rate comes from the replenishment transfer log; the schedule rate from planned production × BOM / 30.
- Hours to minimum = max(0, available − min) / demand × 24.
- **Alert:** available ≤ min. **Predicted:** hours ≤ lead time + safety buffer (3 h + 24 h by default). **Watch:** hours ≤ 72 (dashboard only).
- Suggested quantity = ceil(max − (available − demand × lead / 24)).
- Positions without history or schedule are shown as "No rate" and are not forecast.
- **History window (30 / 45 / 60 days):** the history rate is the transfer quantity in the last N days divided by N. The default is 30. Use the 30 d / 45 d / 60 d buttons on Predicted alerts and Replenishment insights to compare; clicking only previews, and **Save window and send emails** (Predicted alerts) stores the choice. The start-up default can be set with `RATE_WINDOW_DAYS` (30, 45 or 60). On the held-out backtest the longer windows had lower demand-rate error (mean absolute error 4.02, 3.41 and 3.15 units/day for 30, 45 and 60 days), but that tests the daily rate only, not stockout timing, and the default stays 30 until the sponsor confirms.

## Know before you test

- The forecast is an estimate from recorded transfer quantities, not measured consumption. It has not been checked against real stockouts, because no historical bin status exists.
- The default blend and the 3 h lead time are team proposals awaiting sponsor confirmation. Please give feedback on them.
- Notifications are saved as files unless email or Teams is configured (see below). Simulated stock edits never change the source files.
- The server listens on this computer only and has no login. Do not expose it to a network.

## Troubleshooting

- **"Python was not found":** install Python 3.11+ from python.org (tick "Add to PATH"), or ask IT for a managed install.
- **pip cannot download:** point pip at your internal mirror (`pip config set global.index-url ...`) or send the error to the team.
- **"Cannot listen on port 8765":** another program uses it. Start with another port: `run.bat siemens_capstone.sqlite3 8766`.
- **"Database not found":** put `siemens_capstone.sqlite3` next to `run.bat`, or pass its path as the first argument. The script never creates an empty one.
- **Blank page or "unavailable":** the black window must still be open; reload the page.

## Optional: real email, Teams or webhook delivery

Set environment variables before starting (they are read at start-up): `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_FROM`, `ALERT_EMAIL_TO` for email; `TEAMS_WEBHOOK_URL` for a Teams workflow webhook; `ALERT_WEBHOOK_URL` for a generic webhook. Use *Send digest* on Stock alerts to test. Do not commit credentials.

## For maintainers (Git repository only)

- Layout: `index.html` (UI), `server.py`, `src/siemens_capstone/` (API, storage, forecast, analytics), `tests/`, `scripts/`. Maintenance documentation is in `docs/` (architecture, alerts, formulas, Teams setup, design system, data handling); it is not part of the UAT zip.
- The Phase 2 formulas in `docs/formulas.md` are locked: changing them, or their code in `src/siemens_capstone/proactive.py`, needs the owner's explicit permission.
- The repository is public: never commit Siemens workbooks, database files, row-level examples or credentials. Keep them in the ignored `data/` folder.
- Build a workspace from source files: `python server.py --db new.sqlite3 --import-workbook data/Prod_Capstone_Data.xlsx --attach data/Min_Max-SM.xlsx --attach data/BOMEXAMPLE.xlsx --attach Bin_Status_2026-10-22.xlsx --quiet --no-serve`.
- Offline demand-rate backtest: `python scripts/backtest_rates.py --db copy.sqlite3` (aggregate reports in ignored `outputs/`).
- Build the single-file demo (invented data, one HTML file plus a zip): `python scripts/build_demo_html.py`.
- Build the handoff zip: `python scripts/build_uat_zip.py --db data/handoff/siemens_capstone.sqlite3 --handoff`.
- Tests: `pip install -r requirements-dev.txt`, `python -m playwright install chromium`, `python -m pytest -q` (`-m 'not browser'` for backend only).
