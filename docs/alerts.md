# Phase 1 reactive stock alerts and Phase 2 forecast

Open **Stock alerts** with an imported workbook that has a Min/Max reference file attached. Every bin-status position is joined to its tolerance on part + location (and storage type). A position alerts when `available <= min`; a minimum of 0 alerts when the bin is empty. Set `ALERT_RULE=below` for strictly-below (any other value stops the server at startup). Several rows for the same material + storage type + bin (for example several storage units) form one position: identical duplicate rows count once and the remaining segments' available stock is summed, so a bin raises at most one alert. Workbook insights use the same definitions (`at_or_below_min`, zero minimum, `max not set` when max is 0, `min > max` otherwise).

Editing **Available** stores a simulated reading in the `stock_overrides` table. Source files and imported rows are never changed; **Reset simulated stock** removes every override. Every change to the open alerts is notified once, with only the changes, whatever caused it: an edit, **Reset simulated stock**, attaching a Min/Max file or a newer dated snapshot, or the first page load of a dataset. If alerts had changed before an edit (for example an older database), those changes are sent as their own notification before the edit's. An alert resolves when its position recovers, loses its minimum or stock reading, or is no longer in the current snapshot; the reason is stored as `resolution`. **Send digest** sends every open alert. **Send digest** and **Reset simulated stock** need a second click to confirm.

## Delivery channels (all optional, configured with environment variables)

Every channel receives the same payload (`alerts.payload`), so new channels need only an adapter.

| Channel | Variables | Behaviour |
| --- | --- | --- |
| Email (SMTP) | `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_STARTTLS` (1), `SMTP_SSL` (implicit TLS; on automatically for port 465), `ALERT_EMAIL_FROM`, `ALERT_EMAIL_TO` (comma-separated) | Plain-text + HTML email over TLS with certificate and host-name verification. The password is never sent without TLS. When SMTP is not configured or the send fails, the message is saved as an `.eml` file in the repository's `outputs/outbox/` (or `ALERT_OUTBOX`), whatever the database location. |
| Microsoft Teams | `TEAMS_WEBHOOK_URL` | Posts an Adaptive Card for a Teams Workflows "When a Teams webhook request is received" trigger. New, resolved, and digest rows are listed (15 per section, then "+N more"). Setup guide: `docs/teams-setup.md`. |
| Any webhook | `ALERT_WEBHOOK_URL` | Posts the raw JSON payload (Power Automate, Azure Logic Apps, an Industrial Edge service, etc.). |
| Dashboard link | `ALERT_DASHBOARD_URL` | Adds an "Open dashboard" link to email and Teams. |

Example (Gmail needs an app password; never commit secrets):

```sh
SMTP_HOST=smtp.gmail.com SMTP_USER=you@gmail.com SMTP_PASSWORD='app-password' \
ALERT_EMAIL_TO=you@gmail.com .venv/bin/python server.py
```

Failed deliveries are reported in the page and the activity log with the error text (configured passwords and webhook URLs are masked), never raised. Failed deliveries are not retried automatically; use **Send digest** to resend. Unexpected server errors return a JSON 500 and print a traceback to the server's standard error.

## Pages

- **Stock alerts** (Phase 1): Reactive alerts for bins currently at or below their minimum tolerance.
- **Predicted alerts** (Phase 2): Forward-looking forecast with status strip (Predicted, Watch, Below min, No rate), filter lens, hours-to-minimum bar scaled to `watch_hours`, and selected-row projection chart and step-by-step math.
- **Replenishment insights** (View 2): Problem parts table highlighting short refills, frequent refills, and schedule vs. history divergence, with refill reach dots, refill history bar chart, evidence panel, and CSV export.
- **Data sources**: Imports the 4-sheet workbook and attaches reference files (Min/Max tolerances, BOM, dated bin-status snapshots).
- **Settings**: Edits lead times, safety buffer, watch window, demand weights, and divergence threshold. Includes the **Try weights** interactive slider panel with live candidate preview and saved presets.

## API

- Core: `GET /api/datasets`, `GET /api/sources?dataset=`, `GET /api/stock?dataset=`
- Overrides & alerts: `POST /api/stock/available {dataset, position, available|null}`, `POST /api/stock/reset {dataset}`, `POST /api/stock/digest {dataset}`, `POST /api/stock/ack {id}`
- Settings & presets: `POST /api/stock/settings {dataset, ...}`, `POST /api/stock/preview {dataset, ...}`, `GET /api/stock/presets?dataset=`, `POST /api/stock/presets {dataset, name, settings}`, `POST /api/stock/presets/delete {dataset, name}`, `POST /api/stock/lead-class {dataset, position, lead_class}`, `POST /api/stock/check {dataset}`
- View 2: `GET /api/insights/parts?dataset=`, `GET /api/insights/parts.csv?dataset=`

## Phase 2 predicted alerts and the Watch tier

Phase 2 warns before the breach. For each position the app projects when the bin reaches its minimum and compares that with lead time and safety thresholds:

1. **Below minimum (Alert)**: `available <= min`. Kind `below_min`, status `Alert`. Dispatches notifications.
2. **Predicted**: `hours_to_min <= lead_hours + safety_hours`. Kind `predicted`, status `Predicted`. Dispatches notifications.
3. **Watch**: `lead_hours + safety_hours < hours_to_min <= watch_hours`. Status remains `OK`. Suggested replenishment quantity is calculated to help warehouse planners stage materials early. Watch is **dashboard only**: it opens no row in `stock_alerts` and sends no email or Teams notifications. If stock or demand shifts such that a predicted alert enters Watch, the alert resolves with reason `forecast cleared`.
4. **OK**: `hours_to_min > watch_hours`. Status `OK`. Suggested quantity is cleared (`null`).
5. **No rate**: Position has no usable demand inputs (`demand_per_day is null`). Status `OK`.

Positions with no tolerance or no stock reading are not forecast.

### Settings (per dataset, Forecast settings card)

| Setting | Default | Range | Meaning |
| --- | --- | --- | --- |
| `lead_hours_stocked` | 3.0 | 0–720 | Lead time for a stocked item |
| `lead_hours_fab` | 3.0 | 0–720 | Lead time for an item built in fab (sponsor to confirm; see `docs/decisions.md`) |
| `safety_hours` | 24.0 | 0–168 | Extra buffer added to lead time before an alert fires |
| `watch_hours` | 72.0 | 0–720 | Horizon for the Watch tier (visible on dashboard, no notifications) |
| `weights.history` | 1.0 | 0–10 | Weight of historical transfer cadence (`replen_qty_per_day`) |
| `weights.schedule` | 1.0 | 0–10 | Weight of planned production schedule (30-day requirement × BOM / 30) |
| `weights.observed` | 0.0 | 0–10 | Weight of observed depletion between dated snapshots (default 0.0) |
| `divergence_pct` | 50.0 | 0–1000 | When schedule rate differs from history rate by more than this percentage, history is dropped so schedule outweighs it |

Each position has a lead class (`stocked` or `fab`, default `stocked`) selectable in the table; it determines which lead time applies.

**Settings version 2 migration:**
Databases created prior to Phase 2 store version 1 or unversioned settings (`safety_hours = 0.0`, `weights.observed = 1.0`). An idempotent migration in `Store.__init__` automatically updates legacy settings where `safety_hours == 0` to `24.0` and `weights.observed == 1` to `0.0`, marking the record `_version = 2`. User customizations (e.g. non-default lead times or manually adjusted weights) are preserved, and subsequent updates maintain `_version = 2`.

**Formulas:**
Full definitions and worked examples are locked in `docs/formulas.md`. Demand per day is the weighted mean of the available inputs: history (transfer cadence, `replen_qty_per_day`), schedule (planned quantity in the next 30 days / 30, only when the BOM maps the part to exactly one position), and observed (depletion between dated snapshots). Inputs that are missing, negative, or weighted 0 are skipped. `hours_to_min = max(0, available - min) / demand_per_day * 24`. `suggested_qty = ceil(max - (available - demand_per_day * lead_hours / 24))`, floored at 0, when a maximum is set. With no demand rate the forecast is `no_rate` and the status stays OK.

**Lifecycle:**
At most one open alert per position. A predicted alert whose bin then reaches its minimum is **escalated**: the same alert becomes `below_min`, is reopened (even if acknowledged), and is notified again. A below-minimum alert whose stock rises above the minimum but is still within lead time becomes `predicted` without a new notification. A predicted alert that moves to Watch or OK resolves as `forecast cleared`. Email subjects read `[Stock alert] <material> at <supermarket> <bin> predicted to reach minimum in X h`; Teams cards gain a Predicted section.

**Weights preview and presets:**
- `POST /api/stock/preview {dataset, ...settings}`: Evaluates candidate settings without modifying the database, alert table, or notification outbox. Returns candidate vs. current tier counts (`predicted`, `watch`, `below_min`, `no_rate`, `ok`) and up to 50 tier changes sorted by candidate hours to minimum. Debounced at 300 ms in the UI.
- `GET /api/stock/presets?dataset=ID`: Returns saved presets as `[{name, settings, created_at}]`.
- `POST /api/stock/presets {dataset, name, settings}`: Saves or overwrites a named preset (1–40 characters).
- `POST /api/stock/presets/delete {dataset, name}`: Deletes a named preset. Presets are applied to active alerts via `POST /api/stock/settings`.

**Daily bin-status imports and CLI flags:**
Daily bin-status files (`Bin_Status_YYYY-MM-DD.xlsx` with columns `Part, Description, Type, Location, Min, Max, Live Value`) are detected as format `daily_status` under kind `snapshot`.
- Rows are normalized to the standard snapshot schema (`Material, Material Description, Storage Type, Storage Bin, Available stock`).
- The Min/Max tolerance file remains authoritative; differences generate a `tolerance_mismatch` warning finding.
- CLI flags in `server.py`:
  - `--dataset <id>`: Attach reference files directly to an existing dataset without re-importing the workbook.
  - `--quiet`: Sets `announce=False` during import, synchronizing alert records without sending emails or webhooks.
  - `--no-serve`: Exits immediately after import/attach without starting the web server.

**Environment variables:**
`LEAD_HOURS_STOCKED`, `LEAD_HOURS_FAB`, `ALERT_SAFETY_HOURS`, `ALERT_WATCH_HOURS` set defaults for datasets without saved settings (invalid values stop the server at startup). `python server.py --check-minutes N` re-checks every workbook every N minutes (0 = off) so predicted alerts fire without opening the page; the **Run check** button does the same on demand.

The forecast is an estimate from recorded transfer quantities, not measured consumption; it has not been validated against real stockouts.
