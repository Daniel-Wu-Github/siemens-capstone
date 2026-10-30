import pytest
from concurrent.futures import ThreadPoolExecutor

from siemens_capstone.store import Store














def reference_store(tmp_path):
    from test_ingest import workbook_bytes, table_bytes, TOLERANCES, BOM
    store = Store(tmp_path / 'refs.sqlite3')
    ds = store.import_workbook(workbook_bytes(), 'synthetic.xlsx')
    return store, ds['id'], table_bytes, TOLERANCES, BOM


def test_reference_import_dedupes_latest_wins_and_invalidates_cache(tmp_path):
    from test_ingest import SNAPSHOT
    store, dsid, table_bytes, TOL, BOM = reference_store(tmp_path)
    assert store.insights(dsid)['references']['tolerances']['loaded'] is False
    tolerance_file = table_bytes({'Sheet3': TOL})  # Reattach the same bytes; XLSX ZIP timestamps vary between builds.
    result = store.import_reference(dsid, tolerance_file, 't.xlsx')
    assert result['kind'] == 'tolerances' and result['dataset_id'] == dsid and result['rows'] == 2 and result['as_of'] is None
    assert store.insights(dsid)['references']['tolerances']['rows'] == 2  # cache was dropped
    store.import_reference(dsid, tolerance_file, 't.xlsx')
    with store.connection() as db: assert db.execute('SELECT COUNT(*) FROM "references"').fetchone()[0] == 1
    store.import_reference(dsid, table_bytes({'Sheet3': TOL[:2]}), 't2.xlsx')
    assert store.insights(dsid)['references']['tolerances']['rows'] == 1
    store.import_reference(dsid, table_bytes({'S': SNAPSHOT}), 's.xlsx', '2026-10-02')
    store.import_reference(dsid, table_bytes({'S': SNAPSHOT}), 's.xlsx', '2026-10-01')
    assert [s['as_of'] for s in store.insights(dsid)['references']['snapshots']] == ['2026-10-01', '2026-10-02']


def test_reference_import_requires_workbook_dataset_and_valid_file(tmp_path):
    store, dsid, table_bytes, TOL, BOM = reference_store(tmp_path)
    with pytest.raises(ValueError): store.import_reference('demo', table_bytes({'Sheet3': TOL}), 't.xlsx')
    with pytest.raises(ValueError): store.import_reference('missing', table_bytes({'Sheet3': TOL}), 't.xlsx')
    with pytest.raises(ValueError): store.import_reference(dsid, b'junk', 't.xlsx')
    with store.connection() as db: assert db.execute('SELECT COUNT(*) FROM "references"').fetchone()[0] == 0


def test_datasets_lists_only_workbooks_and_ignores_retired_synthetic_rows(tmp_path):
    import json
    path = tmp_path / 'old.sqlite3'
    store = Store(path)
    assert store.datasets() == []
    with store.connection() as db:  # a database created before the synthetic sandbox was retired
        db.execute('INSERT INTO datasets VALUES(?,?,?)', ('demo', json.dumps(dict(id='demo', kind='synthetic')), '{}'))
    assert Store(path).datasets() == []
    with pytest.raises(ValueError): store.sources('demo')
