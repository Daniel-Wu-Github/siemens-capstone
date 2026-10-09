# Data inventory and handling

The supplied `Prod_Capstone_Data.xlsx` lives in the Git-ignored `data/` folder and is never committed. The observations below describe sheet structure only. Row data must not be copied into this public repository.

| Sheet | Approximate rows | Main fields visible in header | Likely prototype use |
| --- | ---: | --- | --- |
| `HISTORICAL_PROD_DATA` | 75,576 | Job and station timestamps, production and sales order IDs, work center, process step, trace duration, status, area | Historical production pace and schedule features |
| `PLANNED_PROD_SCHEDULE` | 6,362 | Sales order, line item, basic start date | Future production load, if it can be joined correctly |
| `HISTORICAL_SM_REPLENISHMENTS` | 30,972 | Material, transfer order, source/destination bin, target quantity, creation and confirmation dates/times | Observed replenishments and lead times |
| `BIN_STATUS_EXAMPLE` | 2,317 | Material, bin, total and available stock, unit, last movement | Example stock snapshot and bin mapping |

Excel stores several dates as serial numbers and times as day fractions. Their timezone, workbook date system, and business meaning must be verified before using them in time based tests.

## Feasibility checks before modeling

1. Confirm whether bin status is a one time example or a history of stock states. A single snapshot cannot establish historical depletion events.
2. Identify the material usage or bill of materials data that connects production orders to individual materials. The production sheet headers do not expose a direct material ID.
3. Establish whether replenishment transactions represent delivery to supermarket bins, transfers within a warehouse, or other movement types; define direction and units.
4. Confirm stable join keys among sales orders, production orders, materials, locations, and planned schedule rows. Quantify unmatched rows rather than silently dropping them.
5. Obtain threshold, bin capacity, warehouse lead time, and event definitions from Siemens. Do not infer a universal threshold from example rows.
6. Document data access, anonymization, permitted storage, retention, and whether any part of the extract may be used in GitHub or external AI tools.

## Repository practice

Keep raw, intermediate, and generated data out of Git. Store configuration examples without secrets. If tests need fixtures, create small synthetic records that do not reproduce Siemens rows or identifiers. Publish only aggregated results after clearance.
