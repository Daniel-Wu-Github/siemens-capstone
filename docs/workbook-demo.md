# Workbook demo

Import the supplied XLSX in Data workspace or with `server.py --import-workbook /path/to/workbook.xlsx`, then select the saved dataset and open Workbook insights. This view runs entirely on the available four-sheet extract and persists its results locally. It has no dependency on live Siemens access or additional data.

## What is directly observed

- Available stock by material, storage type, bin, and unit. Identical stock segments are deduplicated; conflicting duplicate balances make a position ineligible for the scenario.
- Confirmed transfer event counts by week and material. Identical duplicate transfer keys are deduplicated, while conflicting keys, nonpositive quantities, missing identifiers/dates, and reversed confirmation times are excluded.
- Production station activity by month, station event counts, recorded on-hold share, and median passed-event duration. These are station observations rather than completed-product quantities or material-starvation labels.
- Planned sales-order line counts by start month. Both source rows and distinct order/line/date counts are shown. No production quantity or bill of materials is assumed.

The stock table supports search, empty-position and transfer-coverage filters, pagination, and selection into the scenario calculator. Source date ranges make the difference between historical events and future scheduled lines visible. Transfer timing is a recorded creation-to-confirmation interval; zero intervals can reflect automatic confirmation and must not become warehouse travel time.

## Transfer-target proxy

The rate sums positive `Dest.target quantity` from valid confirmed transfers that match exactly one snapshot material/bin and, when supplied, destination storage type. Only PC and EA positions with a valid nonnegative stock balance can use it. Transfer rows lack a unit column, so matching count units remain an explicit assumption.

The denominator is at most thirty calendar days ending at the latest valid confirmation date, including quiet days and potentially partial endpoint days. If the available history is shorter, its observed calendar span is used. Stock divided by this daily target rate is a descriptive ratio, not a measured depletion horizon.

## Saved what-if

Choose a position, usage multiplier, assumed minimum stock, and assumed warehouse lead time. The calculator assumes targets were received, their units match the bin unit, average consumption equals the target rate multiplied by the selected factor, the undated snapshot is the starting balance, and no additional receipts arrive.

The result is `max(0, stock − assumed minimum) / assumed daily usage × 24` hours. It compares that hypothetical interval with the chosen lead time. A result at zero means the snapshot is already at the selected minimum. It does not assign a calendar crossing date, produce a shortage probability, or dispatch a notification.

Results save to a separate SQLite table with the source checksum and analysis version. A refresh retains the latest saved scenario. Source stock and existing recommendations are unchanged. Exported workbook reports contain local material/bin identifiers, aggregate results, assumptions, and recent scenarios.

## Verification and limits

Analytics tests cover source grains, duplicate/conflicting keys, missing balances, invalid units, exclusion counts, calendar-window rates, and persistence. Browser tests exercise the workbook view, saving and refreshing a scenario, searching positions, and preserving source stock. The synthetic model-evaluation scores are never assigned to imported workbooks.

Actual consumption, received quantities, stock history/as-of time, material/BOM mapping, and operating thresholds are still needed before forecasting or operational deployment can be validated.
