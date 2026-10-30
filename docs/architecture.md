# Local architecture

```text
index.html (all CSS, JavaScript, charts)
    │ same-origin HTTP, loopback only
server.py → api.py
    ├── alerts.py / proactive.py: Phase 1 rule, Phase 2 forecast, delivery
    ├── ingest.py: XLSX validation, local date normalization, join coverage
    ├── analytics.py: descriptive workbook summaries (replenishment rates feed Phase 2)
    ├── insights_parts.py: refill metrics and CSV export
    ├── backtest.py: offline held-out demand-rate comparisons
    └── store.py → data/capstone.sqlite3
```

The UI has three main view pages (**Stock alerts**, **Predicted alerts**, **Replenishment insights**) and two setup pages (**Data sources**, **Settings**). The SQLite database is authoritative; browser refreshes do not reset it. Demo creation is idempotent.

## Persistence

| Table | Stored content |
| --- | --- |
| `datasets` | Imported workbook identity, checksum, and quality report (older databases may still hold a retired synthetic row, which is ignored) |
| `records` | Original workbook rows with source sheet and row number; interpreted transfer and schedule timestamps are added as local fields |
| `references` | Attached Min/Max, BOM and dated snapshot files |
| `stock_overrides`, `stock_alerts`, `stock_settings`, `stock_lead_classes`, `notifications` | Simulated stock, alert lifecycle, forecast settings, lead class per position, delivery records |
| `settings_presets` | Named validated settings per dataset (`dataset`, `name`, `created_at`, `payload`); saving a preset does not apply it |
| `activity` | Chronological log of saved changes |
| `analysis_cache` | Descriptive workbook aggregates, keyed by dataset and analytics version; include the source checksum |

SQLite transactions serialize edits. A partial unique index permits only one open stock alert per position and dataset. Stock edits, resets, and reference imports announce each alert change exactly once, except reference imports explicitly marked `announce=False` (`--quiet` in the CLI), which synchronize alerts without delivery.

All source records are kept locally. Original imported row records are not shared; derived position summaries contain material and bin identifiers and must be reviewed before sharing.

## HTTP API

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/` or `/index.html` | Serve the single frontend |
| GET | `/api/health` | Local backend status |
| GET | `/api/datasets` | Saved dataset summaries |
| GET | `/api/sources?dataset=<workbook-id>` | Import quality, attached reference files, and recent import activity |
| GET | `/api/insights?dataset=<workbook-id>` | Cached stock, transfer, production, and schedule summaries |
| GET | `/api/insights/parts?dataset=ID` | Per-position refill evidence, flags (`short_refills`, `frequent_refills`, `divergent`) and summary; optional `refill_pct` (0–100), `refills` (0–100), `divergence` (0–1000), `min_refills` (1–100, default 3: refills needed before a short-refill flag) query overrides |
| GET | `/api/insights/parts.csv?dataset=ID` | Same thresholds, flattened CSV attachment `problem-parts.csv` |
| POST | `/api/stock/preview` | `{dataset, ...settings}` → current/candidate settings and tier counts (`predicted`, `watch`, `below_min`, `no_rate`, `ok`), plus at most 50 tier changes sorted by candidate hours; no writes or delivery |
| GET | `/api/stock/presets?dataset=ID` | Saved presets as `{name, settings, created_at}` records |
| POST | `/api/stock/presets` | `{dataset, name, settings}` saves or replaces a trimmed 1–40 character name; settings validated against current values |
| POST | `/api/stock/presets/delete` | `{dataset, name}` deletes a preset; apply via the existing stock settings route |
| GET/POST | `/api/stock...` | Stock alerts, simulated stock, lead classes, and Phase 2 forecast; see `docs/alerts.md` |
| POST | `/api/import?filename=workbook.xlsx` | Binary XLSX body with `application/octet-stream`; maximum 32 MB |

JSON writes require `application/json`. Host and Origin checks restrict requests to the local server. Paths are explicitly routed, so the database and repository files are not served. SQL uses bound parameters. DOM values from workbook metadata are escaped. Workbook import validates expansion size, duplicate column headers, required sheets/columns, candidate keys, quantities, supported count units, date order, and candidate joins. Same-file import is deduplicated by SHA-256.

## Analytics and Insights Modules

- `insights_parts.py`: Pure computational module (no database I/O) that derives per-position replenishment metrics, evaluates problem part flags, and formats CSV export.
  - Refill sizes use transfer target quantities, not measured receipts.
  - Summary refill percentages pool valid refill events; positions without positive Max do not contribute percentages.
  - Days under minimum remains `pending` until daily historical bin snapshots are available.
  - Window metadata reports the actual analytics window (up to 30 days).
- Daily-status imports (`daily_status` format) store normalized snapshot rows only, excluding their Min/Max fields, with the attached tolerance file remaining authoritative.
- Preview bypasses analysis-cache writes on a cold cache as well as alert synchronization. Tier counts evaluate `predicted`, `watch`, `below_min`, `no_rate`, and `ok`. Presets save complete validated settings but have no alert effect until applied.
- `backtest.py`: Shares validated transfer and production parsing with analytics. Evaluates candidate demand rates against a 30-day held-out transfer history.
- `scripts/backtest_rates.py`: Offline script that reads the selected workbook and BOM from SQLite and emits aggregate Markdown and JSON reports to ignored `outputs/`; it does not import snapshots or modify the database.

## Limits

This is a single-user local development server without authentication, remote deployment, scheduling, email dispatch, or a service-level guarantee. Real source timezones remain unconfirmed. Only PC and EA count units are modeled; other units require explicit conversions. Candidate source keys are reviewed for coverage and not used to assert a validated material-consumption join.
