# Forecasting, workbook analysis, and evaluation

The model scores below describe the synthetic sandbox. Imported workbooks use a separate descriptive analysis and explicitly assumed what-if calculator; they receive no synthetic accuracy score. See [workbook demo](workbook-demo.md) for its source grains and assumptions.

## Estimators

The trailing baseline averages the latest six available hourly consumption observations. The schedule estimator averages units per load over the latest twelve available observations, then multiplies by planned load and the scenario factor. Neither estimator receives observations later than the decision time.

For either estimator, hours to minimum tolerance is `(stock − threshold) / estimated hourly consumption`. Stock already at or below tolerance gives zero hours. Zero consumption above tolerance gives no finite crossing. Stock or consumption over two hours old is stale; a stock snapshot from the future is unknown. Missing history produces no supported forecast. Negative, nonfinite, or unsupported inputs are rejected.

Risk is Critical when the minimum is projected within warehouse lead time; Watch when within lead time plus buffer; otherwise Healthy. Stale and Unknown are distinct states. Scenario factors change planned future demand, not historical observations or the separate evaluation dataset. These rules are demonstration defaults, not sponsor-approved operating policies.

## Holdout protocol

The deterministic generator creates 60 independent synthetic episodes (seed 48209). Each provides twelve prior hourly consumption observations, a stock snapshot, a scheduled load, a three-hour warehouse lead time, and a twelve-hour forecast horizon. Future consumption is generated separately. It is used only to determine the observed crossing time and is never supplied to prediction.

Both estimators receive identical inputs and outcomes. Parameters are fixed before evaluating; there is no selection of model settings on held-out outcomes. The generator explicitly includes a production-load effect, favoring the schedule estimator. Results demonstrate the evaluation machinery and cannot establish Siemens accuracy or generalization to real operations.

## Scoring

- A predicted event is a minimum crossing estimated within twelve hours.
- An actual event is a simulated observed crossing within that same window.
- Precision is true positives divided by predicted events; recall is true positives divided by actual events.
- Actionable recall counts true positives whose observed crossing is at least three hours after the decision, divided by actual events.
- Timing MAE uses every actual event with a finite prediction, even if the predicted time lies outside the horizon. The report includes this sample count.
- Mean warning lead time uses detected events. Coverage is the fraction of episodes with eligible inputs.
- Undefined metrics use `null` and display an em dash, never a misleading zero.

The UI shows both estimators, raw event counts, timing error, a predicted-versus-observed chart, protocol, and example outcomes. The downloadable JSON contains every episode's schedule-estimator outcome, complete metric denominators, a reproducibility checksum, engine version, and saved forecast runs. Repeated evaluation on the same inputs returns identical metrics.

## Requirement traceability

| Requirement | Implemented evidence | Verification / remaining external gate |
| --- | --- | --- |
| R1: Reproducible intake | XLSX upload; persisted source rows, checksum, quality findings, join coverage, and descriptive aggregates | Ingest and analytics tests with synthetic invalid and duplicate rows; source semantics still require Siemens review |
| R2: Depletion baseline | Six-hour trailing average and minimum-crossing calculation | Tests for threshold edges, missing/stale/future data, invalid units and inputs |
| R3: Fair model comparison | Schedule estimator and baseline on identical held-out synthetic scenarios | Determinism, metric counts, outcome separation; real validation awaits historical labels |
| R4: Actionable recommendations | Material/bin, explanation, projected crossing, lead time, priority, acknowledgement, dispatch, receipt | Deduplication, receipt persistence, concurrent-write and rearming tests; operational actionability awaits sponsor UAT |
| R5: Visible review | Single HTML app; material filters, selectable charts, workbook inventory and source charts, saved what-if, comparisons, export | Desktop/mobile browser tests and workbook scenario persistence; sponsor acceptance remains open |

## TDD record

Core forecasting, storage, and import tests were written before the package existed and failed at collection. Their implementations made the initial twenty tests pass. API tests likewise failed before the HTTP module existed. Additional regressions exposed and corrected recommendation ordering; browser checks exercised policy persistence, receipt semantics, imports, exports, and mobile layout. Browser screenshots are saved under ignored `artifacts/`.

Workbook analytics tests cover explicit source grains, duplicate and conflicting rows, calendar windows, unsupported units, scenario persistence, and unchanged imported data. Two additional regressions failed before their fixes: tiny positive target rates were rounded to zero, and a late scenario response could update the UI after a dataset switch. Both were corrected; the complete suite passed 38 tests. A later sandbox change prevented an additional visual recheck of the actual imported workbook; direct SQLite checks verified its saved scenario, source preservation, and local export.
