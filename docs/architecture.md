# Local architecture

```text
index.html (all CSS, JavaScript, charts)
    │ same-origin HTTP, loopback only
server.py → api.py
    ├── alerts.py / proactive.py: Phase 1 rule, Phase 2 forecast, delivery
    ├── ingest.py: XLSX validation, local date normalization, join coverage
    ├── analytics.py: descriptive workbook summaries (replenishment rates feed Phase 2)
    └── store.py → data/capstone.sqlite3
```

The UI has three pages (Stock alerts, Predicted alerts, Replenishment insights, the last not built yet) and two setup pages (Data sources, Settings). The SQLite database is authoritative; browser refreshes do not reset it. Demo creation is idempotent.

## Persistence

| Table | Stored content |
| --- | --- |
| `datasets` | Imported workbook identity, checksum, and quality report (older databases may still hold a retired synthetic row, which is ignored) |
| `records` | Original workbook rows with source sheet and row number; interpreted transfer and schedule timestamps are added as local fields |
| `references` | Attached Min/Max, BOM and dated snapshot files |
| `stock_overrides`, `stock_alerts`, `stock_settings`, `stock_lead_classes`, `notifications` | Simulated stock, alert lifecycle, forecast settings, lead class per position, delivery records |
| `activity` | Chronological log of saved changes |
| `analysis_cache` | Descriptive workbook aggregates, keyed by dataset and analytics version; include the source checksum |

SQLite transactions serialize edits. A partial unique index permits only one open stock alert per position and dataset. Stock edits, resets and reference imports announce each alert change exactly once.

All source records are kept locally. Original imported row records are not shared; derived position summaries contain material and bin identifiers and must be reviewed before sharing.

## HTTP API

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/` or `/index.html` | Serve the single frontend |
| GET | `/api/health` | Local backend status |
| GET | `/api/datasets` | Saved dataset summaries |
| GET | `/api/sources?dataset=<workbook-id>` | Import quality, attached reference files, and recent import activity |
| GET | `/api/insights?dataset=<workbook-id>` | Cached stock, transfer, production, and schedule summaries |
| GET/POST | `/api/stock...` | Stock alerts and Phase 2 forecast; see docs/alerts.md |
| POST | `/api/import?filename=workbook.xlsx` | Binary XLSX body with `application/octet-stream`; maximum 32 MB |

JSON writes require `application/json`. Host and Origin checks restrict requests to the local server. Paths are explicitly routed, so the database and repository files are not served. SQL uses bound parameters. DOM values from workbook metadata are escaped. Workbook import validates expansion size, duplicate column headers, required sheets/columns, candidate keys, quantities, supported count units, date order, and candidate joins. Same-file import is deduplicated by SHA-256.

## Limits

This is a single-user local development server without authentication, remote deployment, scheduling, email dispatch, or a service-level guarantee. Real source timezones remain unconfirmed. Only PC and EA count units are modeled; other units require explicit conversions. Candidate source keys are reviewed for coverage and not used to assert a validated material-consumption join.
