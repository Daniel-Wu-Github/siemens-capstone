# Local architecture

```text
index.html (all CSS, JavaScript, charts)
    │ same-origin HTTP, loopback only
server.py → api.py
    ├── engine.py: point-in-time estimates and synthetic evaluation
    ├── ingest.py: XLSX validation, local date normalization, join coverage
    ├── analytics.py: descriptive workbook summaries and explicit what-if scenarios
    └── store.py → data/capstone.sqlite3
```

The UI has five views: overview, materials and recommendations, model evaluation, data workspace, and workbook insights. Synthetic policy and demand edits recompute recommendations atomically. Workbook scenarios save separate assumptions and results while preserving source rows. The SQLite database is authoritative; browser refreshes do not reset it. Demo creation is idempotent.

## Persistence

| Table | Stored content |
| --- | --- |
| `datasets` | Dataset identity, type, checksum, simulation time/load, quality report |
| `materials` | Synthetic material state, policy, consumption history, snapshot time |
| `records` | Original workbook rows with source sheet and row number; interpreted transfer and schedule timestamps are added as local fields |
| `alerts` | Recommendation identity, material, reason, projected crossing, status, receipt quantity and time |
| `runs` | Forecast outputs, policies, scenario factor, engine version, timestamps |
| `evaluations` | Baseline and candidate metrics, per-episode evidence, protocol, checksum |
| `activity` | Chronological log of saved changes |
| `analysis_cache` | Descriptive workbook aggregates, keyed by dataset and analytics version; include the source checksum |
| `workbook_scenarios` | Selected stock position, assumptions, computed result, source checksum, and timestamp |

SQLite transactions serialize edits. A partial unique index permits only one active recommendation per material/dataset. Receipts validate the prior status and update stock in the same transaction, preventing duplicate or concurrent requests from adding stock twice. A small receipt that leaves the material at risk creates a new recommendation; the prior receipt remains in history.

All source records are kept locally. JSON export includes quality summaries and up to ten recent forecast runs. Workbook exports also include derived position summaries and saved scenarios; these contain material and bin identifiers and must be reviewed before sharing. Original imported row records are not included. Reports from the synthetic dataset include synthetic material histories.

## HTTP API

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/` or `/index.html` | Serve the single frontend |
| GET | `/api/health` | Local backend status |
| GET | `/api/datasets` | Saved dataset summaries |
| GET | `/api/state?dataset=demo` | Current material forecasts, recommendations, latest evaluation, quality and activity |
| GET | `/api/insights?dataset=<workbook-id>` | Cached stock, transfer, production, and schedule summaries, plus recent saved scenarios |
| GET | `/api/export?dataset=demo` | Download a saved-state JSON report |
| POST | `/api/forecast` | Save a run and update recommendations; JSON `{ "dataset": "demo" }` |
| POST | `/api/evaluate` | Evaluate the fixed synthetic holdout and save results; same body |
| POST | `/api/scenario` | JSON `{ "dataset": "demo", "factor": 1.5 }`; factor is 0.25–3 |
| POST | `/api/policy` | Dataset, material ID, and a `policy` object containing `threshold`, `lead_hours`, `buffer_hours` |
| POST | `/api/alert` | Recommendation `id`, next `action`, and integer `quantity` for delivery |
| POST | `/api/import?filename=workbook.xlsx` | Binary XLSX body with `application/octet-stream`; maximum 32 MB |
| POST | `/api/workbook-scenario` | Dataset, `position_id`, `usage_multiplier` (0.1–5), `minimum_stock` (0–1,000,000), and `lead_hours` (0.1–168); saves an assumption-based result without changing source stock |

Alert transitions are Open → Acknowledged → Dispatched → Delivered, with direct Open → Dispatched also supported. This records a simulation and does not contact warehouse personnel.

JSON writes require `application/json`. Host and Origin checks restrict requests to the local server. Paths are explicitly routed, so the database and repository files are not served. SQL uses bound parameters. DOM values from workbook metadata are escaped. Workbook import validates expansion size, duplicate column headers, required sheets/columns, candidate keys, quantities, supported count units, date order, and candidate joins. Same-file import is deduplicated by SHA-256.

## Limits

This is a single-user local development server without authentication, remote deployment, scheduling, email dispatch, or a service-level guarantee. The simulation clock is fixed at September 29, 2026, 12:00 UTC. Real source timezones remain unconfirmed. Only PC and EA count units are modeled; other units require explicit conversions. Candidate source keys are reviewed for coverage and not used to assert a validated material-consumption join.
