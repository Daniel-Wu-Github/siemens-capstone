"""Run the real backend on the mock workbook and export the starting data for the single-file demo."""
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests'), str(ROOT / 'scripts' / 'demo')]
import mock_data  # noqa: E402
from qc_helpers import bom_bytes, full_workbook, make_store, tolerance_bytes  # noqa: E402
from siemens_capstone.alerts import position_key  # noqa: E402


def build_store(tmp):
    d = mock_data.build()
    store = make_store(tmp, 'demo.sqlite3')
    ds = store.import_workbook(full_workbook(d['bins'], production=d['production'], schedule=d['schedule'], transfers=d['transfers']), 'Demo_Workbook.xlsx')['id']
    store.import_reference(ds, tolerance_bytes(d['tolerances']), 'Demo_Min_Max.xlsx')
    store.import_reference(ds, bom_bytes(d['bom']), 'Demo_BOM.xlsx')
    return store, ds


def export(tmp=None):
    tmp = Path(tmp or tempfile.mkdtemp())
    store, ds = build_store(tmp)
    with store.connection() as db:
        inventory = {position_key(r['material'], r['storage_type'], r['bin']): r for r in store._insights(db, ds)['inventory']}
    stock = store.stock_view(ds)
    parts = store.parts_insights(ds)
    for p in stock['positions']:
        p['bom_status'] = inventory.get(p['key'], {}).get('bom_status')
    return dict(dataset_id=ds, datasets=store.datasets(), sources=store.sources(ds), stock=stock, parts=parts,
                presets=store.list_presets(ds)), store, ds


if __name__ == '__main__':
    data, _, _ = export()
    print(json.dumps(data)[:300], len(json.dumps(data)) / 1e6, 'MB')
