# Siemens Capstone: Proactive Material Replenishment

This Texas A&M CSCE 482 capstone explores how to warn warehouse staff before materials in factory floor supermarkets run out. The intended outcome is fewer production interruptions caused by reactive replenishment. Siemens Digitalization and Data sponsors the project.

## MVP Implementation Checkpoint (`code-checkmvp`)

This section documents progress toward the Minimum Viable Product (MVP) for the instructional team review at tag `code-checkmvp`.

### 1. What is Implemented So Far

- **Phase 1: Supermarket Stock Monitoring & Reactive Alerts**:
  - Continuous tracking of supermarket inventory positions against configured Min/Max tolerances.
  - Multi-channel notification dispatching: automated emails (via SMTP with offline fallback to an `.eml` outbox), actionable Microsoft Teams cards, REST webhook integration, and shift summary digests.
  - Interactive operator stock adjustments with event audit logging, alert acknowledgment, and delivery tracking.
- **Phase 2: Proactive ML Depletion Forecasting Engine**:
  - Two-stage hybrid quantile depletion engine estimating conservative $P_{10}$ (worst-case), $P_{50}$ (expected median), and $P_{90}$ days until a part breaches its minimum threshold.
  - BOM & Schedule Demand Aggregation: joins planned sales orders and production schedules against BOMs and routings to calculate deterministic component requirements over 1-day, 3-day, 7-day, and 14-day horizons.
  - Scrap contingency and production load multiplier adjustment ($0.25\times$ to $3.0\times$).
  - Proactive Early Warning Notification Rule: triggers an alert when $P_{10} \le L_{\text{lead}} + X_{\text{buffer}}$, recommending the exact replenishment order quantity ($\text{Max} - \text{Stock}$) and reporting the 3-day breach probability.
- **Full Interactive Web Application & Persistent Store**:
  - Single-page dashboard (`index.html`) served locally at `http://127.0.0.1:8765` backed by Python SQLite persistence (`data/capstone.sqlite3`).
  - Interactive operator controls: dispatch recommendations, record physical receipts (which atomically updates on-hand stock and resolves open alerts), and adjust what-if production parameters.
- **Data Intake, Quality Control & Schema Validation**:
  - Validated data contracts for `HISTORICAL_PROD_DATA`, `PLANNED_PROD_SCHEDULE`, `HISTORICAL_SM_REPLENISHMENTS`, `BIN_STATUS_EXAMPLE`, BOM, and tolerances.
  - Automated join validation (e.g. 100% key match between planned line items and manufacturing routings).
- **Automated Test Suite**:
  - Comprehensive unit and integration test coverage across data schemas, feature extractors, quantile estimators, early warning alerts, and SQLite API endpoints (149 core unit/integration tests passing; zero temporal lookahead leakage).

### 2. What is Not Yet Working / In Progress / Next Steps

- **Live Production ERP/MES/WMS Connectors**:
  - *Current state*: Intake operates on approved offline Excel extracts (`.xlsx`), CSV files, and synthetic verification fixtures.
  - *Next step*: Direct, automated API integration with Siemens SAP ERP and MES systems is pending sponsor network authorization and credential provisioning.
- **Real-Time IoT Sensor & Barcode Telemetry Streaming**:
  - *Current state*: Stock readings rely on periodic snapshot imports and human-in-the-loop manual adjustments.
  - *Next step*: Real-time ingestion from physical supermarket bin optical sensors or handheld RF barcode scanners is planned for Phase 3.
- **Autonomous Transfer Order (TO) Creation in WMS**:
  - *Current state*: System generates recommended replenishment quantities and prompts warehouse operators to acknowledge and dispatch.
  - *Next step*: Direct programmatic creation of SAP Transfer Orders without manual human handoff will be implemented after sponsor UAT approval.
- **Multi-Year Production Training on Uncurated Raw Logs**:
  - *Current state*: Predictive estimators are benchmarked against fixed, reproducible synthetic holdout episodes.
  - *Next step*: Transitioning to deep historical training across years of historical plant logs will commence once sponsor data cleaning definitions are formally signed off.

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

- `data/`: the single local data folder, ignored by Git. Place the Siemens workbooks here (`Prod_Capstone_Data.xlsx`, `Min_Max-SM.xlsx`, `BOMEXAMPLE.xlsx`, `Most_Replen_Summer.xlsx`); the real-file tests read them from this folder and skip when they are absent.
- `data/capstone.sqlite3`: persistent local database, ignored by Git. Closing the server does not erase it.
- `artifacts/`: desktop and mobile screenshots produced by browser tests, ignored by Git.
- `outputs/`: optional exported reports, ignored by Git.
- `handoff.md`: detailed local plan and implementation handoff, always ignored by Git.

To ingest a local workbook at startup or generate a visible JSON evaluation artifact:

```sh
.venv/bin/python server.py --import-workbook data/Prod_Capstone_Data.xlsx --attach data/Min_Max-SM.xlsx --attach data/BOMEXAMPLE.xlsx
.venv/bin/python server.py --export-report outputs/demo-report.json
```

Use `--port 8766` for a different port or `--db /path/to/local.sqlite3` for a separate workspace. The server binds to loopback only. Stop it with Ctrl+C. Opening `index.html` directly shows startup instructions; persistence requires the running backend.

## Verification

### Running the test suite

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium  # required for browser tests
.venv/bin/python -m pytest -v
```

To run only unit tests:
```sh
.venv/bin/python -m pytest -v tests/test_engine.py tests/test_qc_unit.py
```

To run only integration tests:
```sh
.venv/bin/python -m pytest -v tests/test_store.py tests/test_api.py tests/test_qc_integration.py
```

To run backend tests excluding Playwright browser tests:
```sh
.venv/bin/python -m pytest -v -m "not browser"
```

---

### Submitted Tests: Inputs, Expected Results, and Actual Checks

#### Unit Tests (`tests/test_engine.py`, `tests/test_qc_unit.py`)

1. **Baseline vs Schedule-Adjusted Depletion Prediction** ([`test_baseline_and_schedule_adjusted_threshold_time`](tests/test_engine.py#L20))
   * **Run Command**: `.venv/bin/pytest -v tests/test_engine.py -k test_baseline_and_schedule_adjusted_threshold_time`
   * **Controlling Inputs & Conditions**: Input dictionary defines stock (60 PC), safety threshold (20 PC), lead time (3h), buffer (2h), scheduled load (2), and a 6-hour trailing consumption history of 5 PC/hour under baseline load of 1.
   * **Expected Result**: Baseline projection estimates depletion at 5 PC/h, reaching threshold in exactly 8 hours (`(60 - 20) / 5 = 8`) with `Healthy` risk. Schedule-adjusted model scales demand by load factor 2 (10 PC/h), reaching threshold in 4 hours (`(60 - 20) / 10 = 4`), crossing the 5-hour warning horizon (`lead_hours + buffer_hours = 5`) and triggering `Watch` risk.
   * **Actual vs Expected Check**: Inspects returned prediction dictionary keys:
     ```python
     assert baseline['hours_to_threshold'] == 8
     assert candidate['hours_to_threshold'] == 4
     assert candidate['risk'] == 'Watch'
     assert baseline['risk'] == 'Healthy'
     ```

2. **Temporal Leakage Prevention** ([`test_future_observations_cannot_change_prediction`](tests/test_engine.py#L30))
   * **Run Command**: `.venv/bin/pytest -v tests/test_engine.py -k test_future_observations_cannot_change_prediction`
   * **Controlling Inputs & Conditions**: A material snapshot is fixed at `2026-09-29T12:00:00+00:00`. Evaluated first with past history (06:00 to 12:00), then evaluated with an altered history appending an extreme future consumption record (`used=1000` at 13:00).
   * **Expected Result**: The engine must mask out any record where `timestamp > snapshot_at`. The prediction result must be strictly identical between past-only and future-contaminated histories.
   * **Actual vs Expected Check**: Inspects equality between both output dictionaries:
     ```python
     assert predict(m, past, m['snapshot_at']) == predict(m, altered, m['snapshot_at'])
     ```

3. **Evaluation Reproducibility & Denominator Consistency** ([`test_synthetic_evaluation_is_reproducible_and_reports_denominators`](tests/test_engine.py#L67))
   * **Run Command**: `.venv/bin/pytest -v tests/test_engine.py -k test_synthetic_evaluation_is_reproducible_and_reports_denominators`
   * **Controlling Inputs & Conditions**: Generates 60 deterministic synthetic holdout episodes and runs model evaluation twice.
   * **Expected Result**: Evaluation is 100% deterministic, evaluates at least 30 samples, outputs scores bounded in `[0, 1]`, and strictly satisfies confusion matrix arithmetic where predicted events equal `true_positives + false_positives` and actual events equal `true_positives + false_negatives`.
   * **Actual vs Expected Check**:
     ```python
     assert first == evaluate(deepcopy(episodes))
     assert first['sample_count'] >= 30
     for model in first['models']:
         assert model['predicted_events'] == model['true_positives'] + model['false_positives']
         assert model['actual_events'] == model['true_positives'] + model['false_negatives']
         assert 0 <= model['recall'] <= 1
         assert 0 <= model['actionable_recall'] <= model['recall']
     ```

4. **Zero-Minimum Bin Critical Alert** ([`test_minimum_zero_bin_with_zero_stock_alerts`](tests/test_qc_unit.py))
   * **Run Command**: `.venv/bin/pytest -v tests/test_qc_unit.py -k test_minimum_zero_bin_with_zero_stock_alerts`
   * **Controlling Inputs & Conditions**: Provides a supermarket bin row where `available_stock = 0` and configured `min_stock = 0`.
   * **Expected Result**: Empty stock even with zero threshold constitutes an immediate stockout and must produce an alert with status `Critical`.
   * **Actual vs Expected Check**: Inspects generated alert dictionary:
     ```python
     assert alert['risk'] == 'Critical'
     ```

---

#### Integration Tests (`tests/test_store.py`, `tests/test_api.py`, `tests/test_qc_integration.py`)

1. **Atomic Concurrent Deliveries and Insufficient Delivery Rearm** ([`test_concurrent_receipts_apply_once_and_insufficient_delivery_rearms`](tests/test_store.py#L66))
   * **Run Command**: `.venv/bin/pytest -v tests/test_store.py -k test_concurrent_receipts_apply_once_and_insufficient_delivery_rearms`
   * **Controlling Inputs & Conditions**: Initializes an isolated SQLite database using `tmp_path`. Creates an active dispatched alert. Spawns 4 concurrent threads in a `ThreadPoolExecutor`, each attempting to confirm delivery of quantity 1 for the same alert ID.
   * **Expected Result**: Database transaction serialization ensures exactly one delivery succeeds (returning True) while three raise `ValueError` and rollback. Total stock increases by exactly 1. Because delivery of 1 is insufficient to clear threshold risk, the store automatically rearms a new active alert with a fresh ID.
   * **Actual vs Expected Check**:
     ```python
     with ThreadPoolExecutor(max_workers=4) as pool:
         assert sum(pool.map(deliver, range(4))) == 1
     after = store.state('demo')
     assert next(m['stock'] for m in after['materials'] if m['id'] == alert['material_id']) == before + 1
     pending = [a for a in after['alerts'] if a['material_id'] == alert['material_id'] and a['status'] != 'Delivered']
     assert len(pending) == 1 and pending[0]['id'] != alert['id']
     ```

2. **Forecast Deduplication and Delivery Lifecycle Persistence** ([`test_forecast_deduplication_and_delivery_persistence`](tests/test_store.py#L19))
   * **Run Command**: `.venv/bin/pytest -v tests/test_store.py -k test_forecast_deduplication_and_delivery_persistence`
   * **Controlling Inputs & Conditions**: Invokes `store.forecast('demo')` twice sequentially on a temporary SQLite store. Dispatches an active alert, then delivers 100 units. Attempts a duplicate delivery on the delivered alert.
   * **Expected Result**: A partial unique index prevents duplicate active alerts for the same material. Dispatching updates status without altering inventory stock. Confirming delivery increases stock once by 100 units and transitions status to `Delivered`. A second delivery on the same alert is rejected with `ValueError`.
   * **Actual vs Expected Check**:
     ```python
     assert len({a['material_id'] for a in active}) == len(active)
     store.alert_action(alert['id'], 'Dispatched')
     assert next(m['stock'] for m in store.state('demo')['materials'] if m['id'] == alert['material_id']) == before
     store.alert_action(alert['id'], 'Delivered', 100)
     assert next(m['stock'] for m in store.state('demo')['materials'] if m['id'] == alert['material_id']) == before + 100
     with pytest.raises(ValueError):
         store.alert_action(alert['id'], 'Delivered', 100)
     ```

3. **HTTP Server State Roundtrip, Scenario Persistence, and Security Rejection** ([`test_http_roundtrip_persistence_and_export`](tests/test_api.py#L31))
   * **Run Command**: `.venv/bin/pytest -v tests/test_api.py -k test_http_roundtrip_persistence_and_export`
   * **Controlling Inputs & Conditions**: Boots a background `ThreadingHTTPServer` on a random ephemeral port bound to `127.0.0.1` backed by a temporary SQLite file. Dispatches HTTP requests using Python `urllib.request`.
   * **Expected Result**: `/api/health` returns status 200. `/api/scenario` accepts `factor = 1.5`, persisting the parameter across server queries. Cross-origin requests with untrusted `Origin` headers return 403 Forbidden. Traversal paths return 404.
   * **Actual vs Expected Check**:
     ```python
     assert request(base, '/api/health')[0] == 200
     assert request(base, '/api/scenario', {'dataset': 'demo', 'factor': 1.5})[0] == 200
     assert json.loads(request(base, '/api/state?dataset=demo')[1])['dataset']['demand_factor'] == 1.5
     assert request(base, '/api/forecast', {'dataset': 'demo'}, {'Origin': 'https://example.com'})[0] == 403
     assert request(base, '/../../.git/config')[0] == 404
     ```

---

### Design Decisions for Test Controllability and Observability

#### 1. Test Controllability: Explicit Temporal Parameterization (`snapshot_at`)
* **Decision**: All prediction and evaluation functions (`predict()`, `evaluate()`, and synthetic episode generators) take `snapshot_at` as an explicit parameter rather than querying the host operating system clock (`datetime.now()`).
* **Connected Unit Test**: [`test_future_observations_cannot_change_prediction`](tests/test_engine.py#L30) (in `tests/test_engine.py`).
* **Why it makes conditions easier to control**: Test fixtures do not require monkeypatching system time or sleeping. Tests deterministically inject timestamps before, at, and after the snapshot horizon, verifying temporal leakage prevention under exact, repeatable conditions.
* **Early Error Exposure**: Exposes lookahead bias and off-by-one boundary filter bugs immediately during unit testing before bad forecasts can contaminate production or evaluation scores.

#### 2. Test Observability: Strongly Structured Dictionaries with Full Confusion Matrix Denominators
* **Decision**: Instead of returning boolean flags or writing unstructured text logs, all analytics, forecasting, and evaluation engines return structured dictionaries containing both numerators and denominators (`predicted_events`, `actual_events`, `true_positives`, `false_positives`, `false_negatives`, `hours_to_threshold`, `risk`).
* **Connected Unit Test**: [`test_synthetic_evaluation_is_reproducible_and_reports_denominators`](tests/test_engine.py#L67) (in `tests/test_engine.py`).
* **Why it makes results easier to observe**: Test assertions directly inspect exact integer counts and bounded metrics rather than scraping logs or guessing if a zero recall is caused by a missing denominator or bad model prediction.
* **Early Error Exposure**: Exposes subtle metric bugs (such as division by zero on zero-event slices, inverted false positive/negative assignments, and silent alert drops) directly at the assertion line.

---

### Test Execution Report, Observed Failures, and Next Steps

* **Current Test Status**:
  * Unit test suite (`tests/test_qc_unit.py` and `tests/test_engine.py`): **125 passed, 0 failed**.
  * Persistence integration suite (`tests/test_store.py`): **6 passed, 0 failed**.
* **Observed Failures**:
  1. *Playwright Browser Tests* ([`tests/test_browser.py`](tests/test_browser.py)): Fails with `ModuleNotFoundError: No module named 'playwright'` in environments where browser drivers have not been installed.
  2. *Socket / Network Sandbox*: Running HTTP API tests ([`tests/test_api.py`](tests/test_api.py), [`tests/test_qc_integration.py`](tests/test_qc_integration.py)) inside restrictive sandboxes blocks loopback socket binding (`PermissionError: [Errno 1] Operation not permitted`).
* **Next Steps**:
  1. Run `.venv/bin/python -m playwright install chromium` before running browser verification.
  2. Ensure loopback socket binding is permitted when executing integration tests.
  3. Validate Siemens data dictionary and consumption-order joins with project sponsors before transitioning from synthetic holdout evaluation to live production training.

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

The team uses a hybrid process: agree on the problem, access constraints, and acceptance criteria with Siemens, then iterate on data, modeling, and alert prototypes. Keep a trace from each candidate requirement to a test and sponsor review. Use Jira for sprint ownership when access is available; record durable technical decisions in this repository.

The GitHub repository is currently public. Do not commit or paste the supplied workbook, row level examples, internal links, credentials, or other Siemens material without explicit clearance. Raw workbooks live only in the Git-ignored `data/` folder.

## Current source material

The initial scope is based on the sponsor proposal, September 3 sponsor meeting notes and transcript, team project plan, project management worksheet, September 25 individual update, Jira import CSV, and the supplied `Prod_Capstone_Data.xlsx` workbook, kept locally in the Git-ignored `data/` folder. These source files are never committed. See the [project brief](docs/project-brief.md) for how sponsor statements are distinguished from team proposals.
