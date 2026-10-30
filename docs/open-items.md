# Open Items and Deferred Work

This document tracks technical work, evaluations, and sponsor deliverables that have been intentionally deferred. It records the rationale and prerequisites for each item. This document does not track local environment housekeeping or personal credentials (which remain in the untracked `handoff.md`).

Last updated: 2026-10-29.

---

## 1. Machine Learning and Modeling

### ML scoring of presets (Deferred to Sprint 4–5)
- **Status:** Deferred.
- **Context:** The current weights preview interface allows users to adjust demand weights (history, schedule, observed) and divergence thresholds with immediate feedback on predicted and watch counts. Automated scoring or objective optimization of presets was considered but deferred to Sprint 4–5 pending Kyle's review.
- **Prerequisite:** Clear operational loss function (cost of early staging vs. stockout breach) and validated holdout data.

### Aryan's ML engine integration plan (Decision D5)
- **Status:** Deferred to offline validation.
- **Context:** Per Decision D5, the deterministic blend formula defined in `docs/formulas.md` remains the rule of record in the production application and UI. Aryan's ML model will be evaluated offline against held-out real data using the backtesting harness (`src/siemens_capstone/backtest.py` and `scripts/backtest_rates.py`) before any consideration for UI inclusion.
- **Prerequisite:** Completion of offline accuracy benchmarks comparing the ML model against the deterministic blend on holdout sets.

---

## 2. Course and Stakeholder Logistics

### UAT tester count waiver
- **Status:** In progress (Ayad coordinating with Christian).
- **Context:** Course requirements specify a target number of user acceptance testers. Ayad is following up with Christian via email to determine whether the tester quota can be met through Siemens plant personnel or if a course waiver combining Siemens sponsors and project team members can be approved.
- **Prerequisite:** Written confirmation from Christian / TAMU capstone faculty.

---

## 3. Data Deliveries

### Upcoming snapshot files
- **Status:** Awaiting delivery from Christian.
- **Items:**
  1. **October 26 bin-status snapshot:** Expected to follow the `Bin_Status_YYYY-MM-DD.xlsx` format. Once received, it will be backfilled using the quiet CLI importer (`server.py --dataset <id> --quiet --no-serve --attach ...`).
  2. **Historical daily bin status (Jan–May 2026):** Continuous daily stock snapshots across January through May 2026. This data is required to activate the `days_under_min` metric in Replenishment Insights (currently marked `pending`) and validate true historical stockout occurrences.
