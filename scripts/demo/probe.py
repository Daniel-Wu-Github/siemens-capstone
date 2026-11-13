import sys, tempfile, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests'), str(ROOT / 'scripts' / 'demo')]
import mock_data
from qc_helpers import full_workbook, tolerance_bytes, bom_bytes, make_store
d = mock_data.build()
tmp = Path(tempfile.mkdtemp())
store = make_store(tmp)
ds = store.import_workbook(full_workbook(d['bins'], production=d['production'], schedule=d['schedule'], transfers=d['transfers']), 'demo.xlsx')['id']
store.import_reference(ds, tolerance_bytes(d['tolerances']), 'minmax.xlsx')
store.import_reference(ds, bom_bytes(d['bom']), 'bom.xlsx')
v = store.stock_view(ds)
print(len(v['positions']), v['summary'], v['forecast']['summary'], d['flags'])
r = store.insights_parts(ds) if hasattr(store, 'insights_parts') else None
print(r['summary'] if r else [m for m in dir(store) if 'part' in m])
