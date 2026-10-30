# Phase 1 reactive stock alerts

Open **Stock alerts** with an imported workbook that has a Min/Max reference file attached. Every bin-status position is joined to its tolerance on part + location (and storage type). A position alerts when `available <= min`; a minimum of 0 alerts when the bin is empty. Set `ALERT_RULE=below` for strictly-below (any other value stops the server at startup). Several rows for the same material + storage type + bin (for example several storage units) form one position: identical duplicate rows count once and the remaining segments' available stock is summed, so a bin raises at most one alert. Workbook insights use the same definitions (`at_or_below_min`, zero minimum, `max not set` when max is 0, `min > max` otherwise).

Editing **Available** stores a simulated reading in the `stock_overrides` table. Source files and imported rows are never changed; **Reset simulated stock** removes every override. Every change to the open alerts is notified once, with only the changes, whatever caused it: an edit, **Reset simulated stock**, attaching a Min/Max file or a newer dated snapshot, or the first page load of a dataset. If alerts had changed before an edit (for example an older database), those changes are sent as their own notification before the edit's. An alert resolves when its position recovers, loses its minimum or stock reading, or is no longer in the current snapshot; the reason is stored as `resolution`. **Send digest** sends every open alert. **Send digest** and **Reset simulated stock** need a second click to confirm.

## Delivery channels (all optional, configured with environment variables)

Every channel receives the same payload (`alerts.payload`), so new channels need only an adapter.

| Channel | Variables | Behaviour |
| --- | --- | --- |
| Email (SMTP) | `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_STARTTLS` (1), `SMTP_SSL` (implicit TLS; on automatically for port 465), `ALERT_EMAIL_FROM`, `ALERT_EMAIL_TO` (comma-separated) | Plain-text + HTML email over TLS with certificate and host-name verification. The password is never sent without TLS. When SMTP is not configured or the send fails, the message is saved as an `.eml` file in the repository's `outputs/outbox/` (or `ALERT_OUTBOX`), whatever the database location. |
| Microsoft Teams | `TEAMS_WEBHOOK_URL` | Posts an Adaptive Card for a Teams Workflows "When a Teams webhook request is received" trigger. New, resolved, and digest rows are listed (15 per section, then "+N more"). |
| Any webhook | `ALERT_WEBHOOK_URL` | Posts the raw JSON payload (Power Automate, Azure Logic Apps, an Industrial Edge service, etc.). |
| Dashboard link | `ALERT_DASHBOARD_URL` | Adds an "Open dashboard" link to email and Teams. |

Example (Gmail needs an app password; never commit secrets):

```sh
SMTP_HOST=smtp.gmail.com SMTP_USER=you@gmail.com SMTP_PASSWORD='app-password' \
ALERT_EMAIL_TO=you@gmail.com .venv/bin/python server.py
```

Failed deliveries are reported in the page and the activity log with the error text (configured passwords and webhook URLs are masked), never raised. Failed deliveries are not retried automatically; use **Send digest** to resend. Unexpected server errors return a JSON 500 and print a traceback to the server's standard error.

## Pages

**Stock alerts** is Phase 1 only. **Predicted alerts** lists the Phase 2 forecast with a hours-to-minimum bar per row and, for the selected row, a projection chart and the step-by-step calculation (the former Part forecast page and its what-if controls were retired). **Settings** edits the lead times, safety buffer, weights and divergence threshold. **Data sources** imports the workbook and reference files.

## API

`GET /api/datasets`, `GET /api/sources?dataset=`, `GET /api/stock?dataset=`, `POST /api/stock/available {dataset, position, available|null}`, `POST /api/stock/reset {dataset}`, `POST /api/stock/digest {dataset}`, `POST /api/stock/ack {id}`.

## Phase 2 predicted alerts

Phase 2 warns before the breach. For each position the app projects when the bin reaches its minimum and compares that with the lead time to restock it. A position whose projected hours to minimum are at or below `lead hours + safety buffer` becomes **Predicted** and raises an alert of kind `predicted` (Phase 1 alerts have kind `below_min`). Positions with no tolerance or no stock reading are not forecast.

**Settings** (per dataset, Forecast settings card; defaults below):

| Setting | Default | Range | Meaning |
| --- | --- | --- | --- |
| `lead_hours_stocked` | 3 | 0-720 | Lead time for a stocked item |
| `lead_hours_fab` | 3 | 0-720 | Lead time for an item built in fab (sponsor to confirm; see `docs/decisions.md`) |
| `safety_hours` | 0 | 0-168 | Extra buffer added to the lead time |
| `weights.history`, `weights.schedule`, `weights.observed` | 1 each | 0-10 | Weight of each demand input; 0 ignores it |
| `divergence_pct` | 50 | 0-1000 | When the schedule rate differs from the history rate by more than this percentage, history is dropped so the schedule outweighs it |

Each position has a lead class (`stocked` or `fab`, default `stocked`) chosen in the table; it selects which lead time applies.

**Formulas.** Demand per day is the weighted mean of the available inputs: history (transfer cadence, `replen_qty_per_day`), schedule (planned quantity in the next 30 days / 30, only when the BOM maps the part to exactly one position), and observed (depletion between dated snapshots). Inputs that are missing, negative, or weighted 0 are skipped. `hours_to_min = max(0, available - min) / demand_per_day * 24`. `suggested_qty = ceil(max - (available - demand_per_day * lead_hours / 24))`, floored at 0, when a maximum is set. With no demand rate the forecast is `no_rate` and the status stays OK.

**Lifecycle.** At most one open alert per position. A predicted alert whose bin then reaches its minimum is **escalated**: the same alert becomes `below_min`, is reopened (even if acknowledged), and is notified again. A below-minimum alert whose stock rises above the minimum but is still within lead time becomes `predicted` without a new notification. A predicted alert that stops being predicted resolves as `forecast cleared`. Email subjects read `[Stock alert] <material> at <supermarket> <bin> predicted to reach minimum in X h`; Teams cards gain a Predicted section.

**API.** `POST /api/stock/settings {dataset, ...changes}` (partial; `weights` may be partial), `POST /api/stock/lead-class {dataset, position, lead_class}` (`null` clears the override), `POST /api/stock/check {dataset}`. `GET /api/stock` adds `forecast: {settings, summary: {predicted, below_min, no_rate, ok}}` and per-position `lead_class`, `lead_hours`, `demand_per_day`, `demand_basis`, `demand_dropped`, `hours_to_min`, `suggested_qty`, `forecast`.

**Environment.** `LEAD_HOURS_STOCKED`, `LEAD_HOURS_FAB`, `ALERT_SAFETY_HOURS` set the defaults for datasets without saved settings (an invalid value stops the server at startup). `python server.py --check-minutes N` re-checks every workbook every N minutes (0 = off) so predicted alerts fire without opening the page; the **Run check** button does the same on demand.

The forecast is an estimate from recorded transfer quantities, not measured consumption; it has not been validated against real stockouts.
