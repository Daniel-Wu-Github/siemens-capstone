import pytest
from concurrent.futures import ThreadPoolExecutor

from siemens_capstone.store import Store


def test_seed_is_idempotent_and_policies_persist(tmp_path):
    path = tmp_path / 'test.sqlite3'
    store = Store(path)
    initial = store.state('demo')
    assert len(initial['materials']) >= 8
    row = initial['materials'][0]
    store.update_policy('demo', row['id'], {'threshold': 25, 'lead_hours': 4, 'buffer_hours': 1})
    reopened = Store(path).state('demo')
    assert len(reopened['materials']) == len(initial['materials'])
    assert next(m for m in reopened['materials'] if m['id'] == row['id'])['threshold'] == 25


def test_forecast_deduplication_and_delivery_persistence(tmp_path):
    store = Store(tmp_path / 'test.sqlite3')
    store.forecast('demo'); store.forecast('demo')
    state = store.state('demo')
    active = [a for a in state['alerts'] if a['status'] != 'Delivered']
    assert active
    assert len({a['material_id'] for a in active}) == len(active)
    alert = active[0]
    before = next(m['stock'] for m in state['materials'] if m['id'] == alert['material_id'])
    store.alert_action(alert['id'], 'Dispatched')
    assert next(m['stock'] for m in store.state('demo')['materials'] if m['id'] == alert['material_id']) == before
    store.alert_action(alert['id'], 'Delivered', 100)
    after = store.state('demo')
    assert next(m['stock'] for m in after['materials'] if m['id'] == alert['material_id']) == before + 100
    with pytest.raises(ValueError):
        store.alert_action(alert['id'], 'Delivered', 100)
    assert store.state('demo')['activity']


def test_bad_policy_is_atomic_and_scenario_is_persisted(tmp_path):
    store = Store(tmp_path / 'test.sqlite3')
    row = store.state('demo')['materials'][0]
    with pytest.raises(ValueError):
        store.update_policy('demo', row['id'], {'threshold': -1, 'lead_hours': 0, 'buffer_hours': 1})
    assert store.state('demo')['materials'][0]['threshold'] == row['threshold']
    store.scenario('demo', 1.5)
    assert store.state('demo')['dataset']['demand_factor'] == 1.5


def test_evaluation_and_forecasts_saved_for_export(tmp_path):
    store = Store(tmp_path / 'test.sqlite3')
    result = store.evaluate('demo')
    store.forecast('demo')
    assert result['sample_count'] >= 30
    report = store.export('demo')
    assert report['evaluation']['sample_count'] == result['sample_count']
    assert report['runs']
    assert report['dataset']['kind'] == 'synthetic'


def test_alerts_prioritize_earliest_threshold_crossing(tmp_path):
    store = Store(tmp_path / 'priority.sqlite3')
    active = [a for a in store.state('demo')['alerts'] if a['status'] != 'Delivered']
    assert active[0]['risk'] == 'Critical'
    assert [a['crossing_at'] for a in active] == sorted(a['crossing_at'] for a in active)


def test_concurrent_receipts_apply_once_and_insufficient_delivery_rearms(tmp_path):
    store = Store(tmp_path / 'concurrent.sqlite3')
    state = store.state('demo'); alert = state['alerts'][0]
    before = next(m['stock'] for m in state['materials'] if m['id'] == alert['material_id'])
    store.alert_action(alert['id'], 'Dispatched')
    def deliver(_):
        try: store.alert_action(alert['id'], 'Delivered', 1); return True
        except ValueError: return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(deliver, range(4))) == 1
    after = store.state('demo')
    assert next(m['stock'] for m in after['materials'] if m['id'] == alert['material_id']) == before + 1
    pending = [a for a in after['alerts'] if a['material_id'] == alert['material_id'] and a['status'] != 'Delivered']
    assert len(pending) == 1 and pending[0]['id'] != alert['id']


def reference_store(tmp_path):
    from test_ingest import workbook_bytes, table_bytes, TOLERANCES, BOM
    store = Store(tmp_path / 'refs.sqlite3')
    ds = store.import_workbook(workbook_bytes(), 'synthetic.xlsx')
    return store, ds['id'], table_bytes, TOLERANCES, BOM


def test_reference_import_dedupes_latest_wins_and_invalidates_cache(tmp_path):
    from test_ingest import SNAPSHOT
    store, dsid, table_bytes, TOL, BOM = reference_store(tmp_path)
    assert store.insights(dsid)['references']['tolerances']['loaded'] is False
    result = store.import_reference(dsid, table_bytes({'Sheet3': TOL}), 't.xlsx')
    assert result['kind'] == 'tolerances' and result['dataset_id'] == dsid and result['rows'] == 2 and result['as_of'] is None
    assert store.insights(dsid)['references']['tolerances']['rows'] == 2  # cache was dropped
    store.import_reference(dsid, table_bytes({'Sheet3': TOL}), 't.xlsx')
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
