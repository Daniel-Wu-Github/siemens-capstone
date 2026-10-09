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

## API

`GET /api/stock?dataset=`, `POST /api/stock/available {dataset, position, available|null}`, `POST /api/stock/reset {dataset}`, `POST /api/stock/digest {dataset}`, `POST /api/stock/ack {id}`.
