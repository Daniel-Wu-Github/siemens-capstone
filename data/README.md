# data/

Put Siemens files and the local database here. **Everything in this folder except this file and `.gitkeep` is ignored by Git and must never be committed.** The repository is public.

| File | What it is | Where it goes in the app |
| --- | --- | --- |
| `Prod_Capstone_Data.xlsx` | Manufacturing workbook with four sheets: `HISTORICAL_PROD_DATA`, `PLANNED_PROD_SCHEDULE`, `HISTORICAL_SM_REPLENISHMENTS`, `BIN_STATUS_EXAMPLE` | Data sources → Import a manufacturing workbook |
| `Min_Max-SM.xlsx` | Minimum and maximum per bin (`Part`, `Location`, `Min`, `Max`) | Data sources → Reference files |
| `BOMEXAMPLE.xlsx` | Bill of materials (`Item`, `Part`, `Qty`) | Data sources → Reference files |
| `Bin_Status_YYYY-MM-DD.xlsx` | Daily bin status (`Part`, `Location`, `Min`, `Max`, `Live Value`); enter its date as the snapshot date | Data sources → Reference files |
| `capstone.sqlite3` | The local database the app creates and uses by default | Created by `python server.py` |
| `handoff/` | A clean database prepared for sending to Siemens | Used by `scripts/build_uat_zip.py` |

The file names above are the ones this project used. The app detects the type of a reference file from its headers, so other names work.

How to load the files: [docs/DATA_IMPORT_GUIDE.md](../docs/DATA_IMPORT_GUIDE.md). Setup and start: [docs/INSTALL_GUIDE.md](../docs/INSTALL_GUIDE.md).

To start from nothing, stop the server and delete `capstone.sqlite3`.
