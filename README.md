# Siemens Capstone: Proactive Material Replenishment

Texas A&M CSCE 482 capstone for Siemens Digitalization and Data. The prototype warns warehouse staff before supermarket bins run out: it flags bins already below minimum, predicts which bins will get there soon, and shows which parts have replenishment problems. It runs on one computer, in a browser, from one database file.

## Quick start (UAT build)

**You need:** Python 3.11 or newer, and the files from the zip in one folder: `index.html`, `server.py`, `src/`, `requirements.txt`, `run.bat`, `run.sh`, this README, and `siemens_uat.sqlite3` (the database, in the same folder as this README).

**Start it**

- **Windows:** double-click `run.bat`.
- **Mac / Linux:** open a terminal in this folder and run `sh run.sh`.

The first start creates a private `.venv` folder and installs one package (`openpyxl`), which needs internet access or an internal pip mirror. Later starts take a few seconds. Your browser opens **http://127.0.0.1:8765**. Keep the black window open while testing; close it, or press Ctrl+C, to stop. Your edits are kept in the database file.

**Options** (both scripts take the same two optional arguments):

```
run.bat                              database siemens_uat.sqlite3 in this folder, port 8765
run.bat my_copy.sqlite3              a different database file
run.bat siemens_uat.sqlite3 8766     a different port
```

If a script cannot be used, run it by hand after `python -m venv .venv` and `pip install -r requirements.txt` (use `.venv\Scripts\python` on Windows, `.venv/bin/python` elsewhere): `python server.py --db siemens_uat.sqlite3`.

**Five-minute tour** (left menu)

1. **Stock alerts:** every bin with its stock, minimum and maximum. Click a column heading to sort, use the search box and filter. Type a lower number in *Available* and press Enter (or *Set*) to simulate a breach: an alert opens and an email is saved as a file in `outputs/outbox` (nothing is emailed). *Reset* on a row, or *Reset simulated stock*, puts it back. *Acknowledge all below-minimum* marks the open below-minimum alerts as seen without changing stock.
2. **Predicted alerts:** bins forecast to reach their minimum within lead time + safety buffer (Predicted), and within 72 hours (Watch). Click a row to see how it was calculated. Positions with no demand history show why they have no rate.
3. **Replenishment insights:** parts with replenishment problems. Click the flag buttons (short refills, frequent refills, divergent) to filter; change the thresholds live; export CSV.
4. **Data sources:** import the Siemens workbook and reference files (Min/Max, BOM, daily bin status named `Bin_Status_YYYY-MM-DD.xlsx`). Already loaded in the supplied database.
5. **Settings:** lead times, safety buffer, watch window and the **history share** slider (schedule gets the rest). The formulas and the tier counters update live before you save. Presets can be saved and applied.

## How the numbers work

- Demand per day = history share × history rate + schedule share × schedule rate. Default is history 2 : schedule 1 with the "differs by more than X %" override switched off (Settings). The history rate comes from the replenishment transfer log; the schedule rate from planned production × BOM / 30.
- Hours to minimum = max(0, available − min) / demand × 24.
- **Alert:** available ≤ min. **Predicted:** hours ≤ lead time + safety buffer (3 h + 24 h by default). **Watch:** hours ≤ 72 (dashboard only).
- Suggested quantity = ceil(max − (available − demand × lead / 24)).
- Positions without history or schedule are shown as "No rate" and are not forecast.

## Know before you test

- The forecast is an estimate from recorded transfer quantities, not measured consumption. It has not been checked against real stockouts, because no historical bin status exists.
- The default blend and the 3 h lead time are team proposals awaiting sponsor confirmation. Please give feedback on them.
- Notifications are saved as files unless email or Teams is configured (see below). Simulated stock edits never change the source files.
- The server listens on this computer only and has no login. Do not expose it to a network.

## Troubleshooting

- **"Python was not found":** install Python 3.11+ from python.org (tick "Add to PATH"), or ask IT for a managed install.
- **pip cannot download:** point pip at your internal mirror (`pip config set global.index-url ...`) or send the error to the team.
- **"Cannot listen on port 8765":** another program uses it. Start with another port: `run.bat siemens_uat.sqlite3 8766`.
- **"Database not found":** put `siemens_uat.sqlite3` next to `run.bat`, or pass its path as the first argument. The script never creates an empty one.
- **Blank page or "unavailable":** the black window must still be open; reload the page.

## Optional: real email, Teams or webhook delivery

Set environment variables before starting (they are read at start-up): `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_FROM`, `ALERT_EMAIL_TO` for email; `TEAMS_WEBHOOK_URL` for a Teams workflow webhook; `ALERT_WEBHOOK_URL` for a generic webhook. Use *Send digest* on Stock alerts to test. Do not commit credentials.

## For maintainers (Git repository only)

- Layout: `index.html` (UI), `server.py`, `src/siemens_capstone/` (API, storage, forecast, analytics), `tests/`, `scripts/`. Maintenance documentation is in `docs/` (architecture, alerts, formulas, Teams setup, design system, data handling); it is not part of the UAT zip.
- The Phase 2 formulas in `docs/formulas.md` are locked: changing them, or their code in `src/siemens_capstone/proactive.py`, needs the owner's explicit permission.
- The repository is public: never commit Siemens workbooks, database files, row-level examples or credentials. Keep them in the ignored `data/` folder.
- Build a workspace from source files: `python server.py --db new.sqlite3 --import-workbook data/Prod_Capstone_Data.xlsx --attach data/Min_Max-SM.xlsx --attach data/BOMEXAMPLE.xlsx --attach Bin_Status_2026-10-22.xlsx --quiet --no-serve`.
- Offline demand-rate backtest: `python scripts/backtest_rates.py --db copy.sqlite3` (aggregate reports in ignored `outputs/`).
- Build the UAT zip: `python scripts/build_uat_zip.py --db data/handoff/siemens_uat.sqlite3`.
- Tests: `pip install -r requirements-dev.txt`, `python -m playwright install chromium`, `python -m pytest -q` (`-m 'not browser'` for backend only).
