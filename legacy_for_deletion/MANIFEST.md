# Legacy code and docs, kept for deletion review

Retired on 2026-10-29 when the app was reduced to three pages (Stock alerts, Predicted alerts, Replenishment insights) plus Data sources and Settings. Nothing in this folder is imported, served or tested: `pytest.ini` only collects `tests/`, and `server.py` adds only `src/` to the import path. Paths mirror the repository root. Files ending in `.txt` hold excerpts removed from files that are still live.

Review each row, delete what you do not want, and commit the deletion yourself. Nothing here has been committed.

| Path | What it was | Why retired |
| --- | --- | --- |
| `index.html` | The previous frontend: Overview, Materials & alerts, Model evaluation, Workbook insights, Part forecast, Data workspace and the old Stock alerts page | Replaced by the new `index.html` (design in `docs/frontend_design_system.md`) |
| `src/siemens_capstone/engine.py` | Synthetic factory sandbox: schedule estimator, baseline, 60 held-out simulated episodes | Synthetic demo, already demoed |
| `src/siemens_capstone/store_removed_methods.txt` | `Store` methods for the sandbox (policies, scenarios, recommendations, evaluation, export, workbook scenarios) and the startup code that created the `demo` dataset | Legacy |
| `src/siemens_capstone/analytics_scenario.txt` | `analytics.scenario()`, the what-if calculator | Legacy; the rest of `analytics.py` stays because Phase 2 uses its replenishment rates |
| `docs/evaluation.md`, `docs/workbook-demo.md` | Documentation of the synthetic evaluation and the workbook demo | Describe retired features |
| `tests/test_engine.py`, `tests/test_forecast_dashboard.py` | Tests for the sandbox engine and the old dashboard | Legacy |
| `tests/test_analytics.py.removed.txt`, `tests/test_store.py.removed.txt`, `tests/test_api.py.removed.txt` | Scenario, sandbox and old-route tests removed from the live test files | Legacy |
| `tests/test_browser.py.before-redesign.txt`, `tests/test_phase2_browser.py.before-redesign.txt` | Browser tests for the old UI | Replaced by tests for the new UI |

## Behaviour removed
- API routes: `GET /api/state`, `GET /api/export`; `POST /api/forecast`, `/api/evaluate`, `/api/scenario`, `/api/policy`, `/api/alert`, `/api/workbook-scenario`. New: `GET /api/sources`.
- `server.py --export-report` option.
- The SQLite tables `materials`, `alerts`, `runs`, `evaluations`, `workbook_scenarios` are no longer created. Existing databases keep them untouched and unread.
- The app no longer creates a synthetic `demo` dataset; `Store.datasets()` lists workbooks only, and the frontend uses the most recently imported workbook.
