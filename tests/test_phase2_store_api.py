"""Phase 2 store + HTTP tests on synthetic workbooks that carry replenishment transfers (no SMTP, temp DBs only)."""
from datetime import datetime
import math
import time

import pytest

from qc_helpers import bom_bytes, call, eml_count, full_workbook, make_store, serving, tolerance_bytes

SM_ROWS = ('Material', 'Material Description', 'Storage Type', 'Storage Bin', 'Available stock', 'Base Unit of Measure')

# Positions (all storage type 401, unit PC). Transfers give PRED, SAFE and BM a replen rate of 30 per day.
#   PRED   13 / min 10 / max 100 -> 0.8 h per unit above min -> 2.4 h  -> Predicted at the 3 h default
#   SAFE   60 / min 10 / max 100 -> 40 h                              -> OK (forecast ok)
#   NORATE 50 / min 10 / max 100, no transfers                         -> OK (forecast no_rate)
#   BM      5 / min 10 / max 100                                       -> Alert (below_min)
PRED, SAFE, NORATE, BM = 'PRED|401|BP', 'SAFE|401|BS', 'NORATE|401|BN', 'BM|401|BB'
BINS = [['PRED', 'Synthetic p', '401', 'BP', 13, 'PC'], ['SAFE', 'Synthetic s', '401', 'BS', 60, 'PC'],
        ['NORATE', 'Synthetic n', '401', 'BN', 50, 'PC'], ['BM', 'Synthetic b', '401', 'BB', 5, 'PC']]
TOLS = [['PRED', 'x', 401, 'BP', 10, 100], ['SAFE', 'x', 401, 'BS', 10, 100], ['NORATE', 'x', 401, 'BN', 10, 100], ['BM', 'x', 401, 'BB', 10, 100]]


def transfer(n, material, bin_, qty, day):
    d = datetime(2026, 10, day)
    return [f'T{n}', 1, material, '001', '401', bin_, qty, d, 0, d, 0]


# Oct 1 and Oct 10 -> 10 day window; 150 + 150 over 10 days = 30 per day for each of three materials.
TRANSFERS = [transfer(i * 2 + k, m, b, 150, d) for i, (m, b) in enumerate([('PRED', 'BP'), ('SAFE', 'BS'), ('BM', 'BB')])
             for k, d in enumerate((1, 10))]
PRODUCTION = [['SO1', '0010', 'J1', 'PO1', 70, datetime(2026, 10, 9), datetime(2026, 10, 10), 'Passed', 'ST1']]


def workbook(bins=BINS, transfers=TRANSFERS):
    return full_workbook(bins, production=PRODUCTION, transfers=transfers)


def build(tmp_path, bins=BINS, tols=TOLS, transfers=TRANSFERS, name='p2.sqlite3'):
    store = make_store(tmp_path, name)
    ds = store.import_workbook(workbook(bins, transfers), 'synthetic.xlsx')['id']
    if tols is not None: store.import_reference(ds, tolerance_bytes(tols), 'minmax.xlsx')
    return store, ds


def n(x):
    """Count whether the API returns a number or a list."""
    return len(x) if isinstance(x, (list, tuple)) else x


def by_material(view):
    return {p['material']: p for p in view['positions']}


def open_by_position(view):
    return {a['position']: a for a in view['open_alerts']}


def resolved_rows(store, ds):
    import json
    with store.connection() as db:
        return [json.loads(r['payload']) for r in db.execute("SELECT payload FROM stock_alerts WHERE dataset=? AND status='Resolved'", (ds,))]


@pytest.fixture
def ready(tmp_path):
    store, ds = build(tmp_path)
    return store, ds, tmp_path / 'outbox'


# ---------------------------------------------------------------- fixture sanity (analytics gives the expected rates)
def test_fixture_produces_known_replen_rate(ready):
    store, ds, _ = ready
    rows = {r['material']: r for r in store.insights(ds)['inventory']}
    for m in ('PRED', 'SAFE', 'BM'): assert rows[m]['replen_qty_per_day'] == pytest.approx(30.0), m
    assert rows['NORATE']['replen_qty_per_day'] is None


# ---------------------------------------------------------------- stock_view
def test_stock_view_has_forecast_block_and_position_fields(ready):
    store, ds, _ = ready
    view = store.stock_view(ds)
    assert view['forecast']['settings'] == store.stock_settings(ds)
    assert view['forecast']['summary'] == dict(predicted=1, below_min=1, no_rate=1, ok=1)
    p = by_material(view)['PRED']
    assert p['status'] == 'Predicted' and p['forecast'] == 'within_lead'
    assert p['lead_class'] == 'stocked' and p['lead_hours'] == 3.0
    assert p['demand_per_day'] == pytest.approx(30.0) and p['demand_basis'] == ['history'] and p['demand_dropped'] == []
    assert p['hours_to_min'] == 2.4 and p['suggested_qty'] == math.ceil(100 - (13 - 30 * 3 / 24)) == 91
    safe, norate, bm = (by_material(view)[m] for m in ('SAFE', 'NORATE', 'BM'))
    assert (safe['status'], safe['forecast'], safe['hours_to_min']) == ('OK', 'ok', 40.0) and safe['suggested_qty'] is None
    assert (norate['status'], norate['forecast'], norate['hours_to_min']) == ('OK', 'no_rate', None)
    assert (bm['status'], bm['forecast'], bm['hours_to_min']) == ('Alert', 'below_min', 0)
    assert bm['suggested_qty'] == math.ceil(100 - (5 - 30 * 3 / 24)) == 99


def test_phase1_summary_keys_unchanged_and_ok_excludes_predicted(ready):
    store, ds, _ = ready
    s = store.stock_view(ds)['summary']
    assert set(s) == {'positions', 'alerts', 'ok', 'no_tolerance', 'overridden'}
    assert s == dict(positions=4, alerts=1, ok=2, no_tolerance=0, overridden=0)       # PRED is Predicted, not OK


def test_no_transfers_means_no_predicted_alerts(tmp_path):
    store, ds = build(tmp_path, transfers=[])
    view = store.stock_view(ds)
    assert view['forecast']['summary'] == dict(predicted=0, below_min=1, no_rate=3, ok=0)
    assert {a['kind'] for a in view['open_alerts']} == {'below_min'}


def test_positions_without_tolerance_have_no_forecast(tmp_path):
    store, ds = build(tmp_path, tols=[TOLS[0]])
    p = by_material(store.stock_view(ds))['SAFE']
    assert p['status'] == 'No tolerance' and p['forecast'] is None and p['hours_to_min'] is None and p['suggested_qty'] is None
    assert store.stock_view(ds)['forecast']['summary'] == dict(predicted=1, below_min=0, no_rate=0, ok=0)


# ---------------------------------------------------------------- predicted alert lifecycle
def test_predicted_alert_opens_and_notifies_exactly_once(ready):
    store, ds, outbox = ready
    view = store.stock_view(ds)
    kinds = {a['position']: a['kind'] for a in view['open_alerts']}
    assert kinds == {PRED: 'predicted', BM: 'below_min'}
    assert [x['summary']['new'] for x in view['notifications']] == [2]               # announced when tolerances were attached
    assert eml_count(outbox) == 1
    note = view['notifications'][0]
    assert 'PRED' in note['text'] and 'reaches min in' in note['text']
    pred = open_by_position(view)[PRED]
    assert pred['hours_to_min'] == 2.4 and pred['lead_hours'] == 3.0 and pred['suggested_qty'] == 91
    assert pred['demand_per_day'] == pytest.approx(30.0) and pred['status'] == 'Open'


def test_repeat_view_creates_no_new_notification(ready):
    store, ds, outbox = ready
    first = store.stock_view(ds)
    again = store.stock_view(ds); third = store.stock_view(ds)
    assert again['notification'] is None and third['notification'] is None
    assert len(third['notifications']) == len(first['notifications']) == 1
    assert eml_count(outbox) == 1
    assert [a['id'] for a in third['open_alerts']] == [a['id'] for a in first['open_alerts']]
    check = store.run_stock_check(ds)
    assert n(check['new']) == 0 and n(check['resolved']) == 0 and check['notification'] is None
    assert eml_count(outbox) == 1


def test_stock_drop_to_below_min_escalates_same_alert_and_reopens_acknowledged(ready):
    store, ds, outbox = ready
    captured = []
    real = store._notify
    store._notify = lambda dataset, as_of, new, resolved, open_alerts, digest=False: (captured.append(new), real(dataset, as_of, new, resolved, open_alerts, digest))[1]
    before = open_by_position(store.stock_view(ds))[PRED]
    store.acknowledge_stock_alert(before['id'])
    assert open_by_position(store.stock_view(ds))[PRED]['status'] == 'Acknowledged'
    mails = eml_count(outbox)
    result = store.set_available(ds, PRED, 10)                                       # available == min -> Alert
    assert n(result['new']) == 1 and n(result['resolved']) == 0 and result['notification']
    assert eml_count(outbox) == mails + 1
    view = store.stock_view(ds)
    after = open_by_position(view)[PRED]
    assert after['id'] == before['id'] and after['kind'] == 'below_min'
    assert after['status'] == 'Open' and after.get('escalated_at')
    assert len([a for a in view['open_alerts'] if a['position'] == PRED]) == 1
    assert any(a.get('escalated') is True and a['id'] == before['id'] for batch in captured for a in batch)
    assert 'is at or below minimum' in result['notification']['subject']
    assert view['notification'] is None


def test_below_min_downgrade_to_predicted_is_silent_and_keeps_alert_open(ready):
    store, ds, outbox = ready
    store.set_available(ds, PRED, 10)                                                # escalate
    alert_id = open_by_position(store.stock_view(ds))[PRED]['id']
    mails = eml_count(outbox)
    result = store.set_available(ds, PRED, 12.5)                                     # 2.5 above min -> 2 h -> Predicted
    assert n(result['new']) == 0 and n(result['resolved']) == 0 and result['notification'] is None
    assert eml_count(outbox) == mails
    now_open = open_by_position(store.stock_view(ds))[PRED]
    assert now_open['id'] == alert_id and now_open['kind'] == 'predicted'
    assert by_material(store.stock_view(ds))['PRED']['status'] == 'Predicted'


def test_same_kind_update_refreshes_snapshot_without_notification(ready):
    store, ds, outbox = ready
    mails = eml_count(outbox)
    result = store.set_available(ds, PRED, 12)                                       # still predicted, 1.6 h
    assert n(result['new']) == 0 and result['notification'] is None and eml_count(outbox) == mails
    a = open_by_position(store.stock_view(ds))[PRED]
    assert a['available'] == 12 and a['hours_to_min'] == 1.6 and a['kind'] == 'predicted'


def test_raising_stock_resolves_predicted_as_forecast_cleared_and_below_min_as_recovered(ready):
    store, ds, outbox = ready
    result = store.set_available(ds, PRED, 100)
    assert n(result['resolved']) == 1 and result['notification'] and 'resolved' in result['notification']['subject']
    result = store.set_available(ds, BM, 100)
    assert n(result['resolved']) == 1
    reasons = {r['position']: r['resolution'] for r in resolved_rows(store, ds)}
    assert reasons == {PRED: 'forecast cleared', BM: 'recovered'}
    view = store.stock_view(ds)
    assert view['open_alerts'] == []


def test_escalated_then_recovered_alert_resolves_as_recovered(ready):
    store, ds, _ = ready
    store.set_available(ds, PRED, 10)
    store.set_available(ds, PRED, 100)
    assert {r['position']: r['resolution'] for r in resolved_rows(store, ds)}[PRED] == 'recovered'


def test_new_predicted_after_recovery_opens_a_fresh_alert(ready):
    store, ds, _ = ready
    first = open_by_position(store.stock_view(ds))[PRED]['id']
    store.set_available(ds, PRED, 100)
    result = store.set_available(ds, PRED, 12)
    assert n(result['new']) == 1 and result['notification']
    assert 'predicted to reach minimum' in result['notification']['subject'] or 'PRED' in result['notification']['text']
    assert open_by_position(store.stock_view(ds))[PRED]['id'] != first


# ---------------------------------------------------------------- settings
def test_update_stock_settings_changes_lead_time_opens_and_resolves_predicted(ready):
    store, ds, outbox = ready
    assert store.stock_settings(ds)['lead_hours_stocked'] == 3.0
    mails = eml_count(outbox)
    result = store.update_stock_settings(ds, {'lead_hours_stocked': 48})
    assert result['settings']['lead_hours_stocked'] == 48.0
    assert n(result['new']) == 1 and n(result['resolved']) == 0 and result['notification']  # SAFE (40 h) now inside 48 h
    assert eml_count(outbox) == mails + 1
    view = store.stock_view(ds)
    assert view['forecast']['summary']['predicted'] == 2 and view['notification'] is None
    safe = by_material(view)['SAFE']
    assert safe['status'] == 'Predicted' and safe['lead_hours'] == 48.0 and open_by_position(view)[SAFE]['kind'] == 'predicted'

    result = store.update_stock_settings(ds, {'lead_hours_stocked': 1})
    assert n(result['resolved']) == 2                                                  # SAFE and PRED (2.4 h > 1 h)
    reasons = {r['position']: r['resolution'] for r in resolved_rows(store, ds)}
    assert reasons == {SAFE: 'forecast cleared', PRED: 'forecast cleared'}
    assert store.stock_view(ds)['forecast']['summary']['predicted'] == 0


def test_safety_buffer_setting_widens_the_window(ready):
    store, ds, _ = ready
    store.update_stock_settings(ds, {'lead_hours_stocked': 1})
    assert by_material(store.stock_view(ds))['PRED']['status'] == 'OK'
    result = store.update_stock_settings(ds, {'safety_hours': 2})                      # threshold 3 h >= 2.4 h
    assert n(result['new']) == 1
    assert by_material(store.stock_view(ds))['PRED']['status'] == 'Predicted'


def test_settings_persist_across_store_instances(ready, tmp_path):
    store, ds, _ = ready
    store.update_stock_settings(ds, {'lead_hours_stocked': 48, 'weights': {'observed': 0}})
    from siemens_capstone.store import Store
    again = Store(store.path, alert_config=store.alert_config)
    s = again.stock_settings(ds)
    assert s['lead_hours_stocked'] == 48.0 and s['weights']['observed'] == 0.0 and s['weights']['history'] == 1.0
    assert again.stock_view(ds)['forecast']['summary']['predicted'] == 2


def test_default_settings_when_no_row(ready):
    store, ds, _ = ready
    s = store.stock_settings(ds)
    assert (s['lead_hours_stocked'], s['lead_hours_fab'], s['safety_hours'], s['divergence_pct']) == (3.0, 3.0, 0.0, 50.0)


def test_invalid_settings_are_rejected_and_nothing_changes(ready):
    store, ds, outbox = ready
    before = store.stock_settings(ds); mails = eml_count(outbox)
    for bad in ({'lead_hours_stocked': -1}, {'lead_hours_stocked': 721}, {'lead_hours_fab': 'abc'}, {'safety_hours': float('nan')},
                {'nope': 1}, {'weights': {'history': 11}}, {'divergence_pct': True}, {'lead_hours_stocked': None}):
        with pytest.raises(ValueError): store.update_stock_settings(ds, bad)
    assert store.stock_settings(ds) == before and eml_count(outbox) == mails


def test_settings_reject_non_workbook_dataset(ready):
    store, ds, _ = ready
    with pytest.raises(ValueError): store.update_stock_settings('demo', {'lead_hours_stocked': 5})
    with pytest.raises(ValueError): store.update_stock_settings('does-not-exist', {'lead_hours_stocked': 5})


def test_settings_are_per_dataset(tmp_path):
    store, ds = build(tmp_path)
    other = store.import_workbook(workbook(transfers=[]), 'other.xlsx')['id']
    store.update_stock_settings(ds, {'lead_hours_stocked': 9})
    assert store.stock_settings(other)['lead_hours_stocked'] == 3.0


# ---------------------------------------------------------------- lead class
def test_fab_lead_class_with_larger_fab_lead_triggers_predicted_and_none_removes(ready):
    store, ds, outbox = ready
    store.update_stock_settings(ds, {'lead_hours_fab': 48})
    assert by_material(store.stock_view(ds))['SAFE']['status'] == 'OK'                 # still stocked
    mails = eml_count(outbox)
    result = store.set_lead_class(ds, SAFE, 'fab')
    assert n(result['new']) == 1 and result['notification'] and eml_count(outbox) == mails + 1
    safe = by_material(store.stock_view(ds))['SAFE']
    assert safe['lead_class'] == 'fab' and safe['lead_hours'] == 48.0 and safe['status'] == 'Predicted'
    assert safe['suggested_qty'] == math.ceil(100 - (60 - 30 * 48 / 24))               # 100 - (60 - 60)
    result = store.set_lead_class(ds, SAFE, None)
    assert n(result['resolved']) == 1
    safe = by_material(store.stock_view(ds))['SAFE']
    assert safe['lead_class'] == 'stocked' and safe['status'] == 'OK'
    assert {r['position']: r['resolution'] for r in resolved_rows(store, ds)}[SAFE] == 'forecast cleared'


def test_lead_class_persists_and_stocked_can_be_set_explicitly(ready):
    store, ds, _ = ready
    store.set_lead_class(ds, NORATE, 'fab')
    from siemens_capstone.store import Store
    assert by_material(Store(store.path, alert_config=store.alert_config).stock_view(ds))['NORATE']['lead_class'] == 'fab'
    store.set_lead_class(ds, NORATE, 'stocked')
    assert by_material(store.stock_view(ds))['NORATE']['lead_class'] == 'stocked'
    store.set_lead_class(ds, SAFE, None)                                               # removing a missing override is fine


def test_set_lead_class_errors(ready):
    store, ds, outbox = ready
    mails = eml_count(outbox)
    with pytest.raises(ValueError): store.set_lead_class(ds, SAFE, 'warehouse')
    with pytest.raises(ValueError): store.set_lead_class(ds, SAFE, '')
    with pytest.raises(ValueError): store.set_lead_class(ds, 'NOPE|401|X', 'fab')
    with pytest.raises(ValueError): store.set_lead_class('demo', SAFE, 'fab')
    assert by_material(store.stock_view(ds))['SAFE']['lead_class'] == 'stocked' and eml_count(outbox) == mails


# ---------------------------------------------------------------- run_stock_check
def test_run_stock_check_returns_checked_at_and_picks_up_changes(ready):
    store, ds, outbox = ready
    result = store.run_stock_check(ds)
    assert set(result) >= {'new', 'resolved', 'notification', 'checked_at'}
    assert result['checked_at'] and datetime.fromisoformat(result['checked_at'].replace('Z', '+00:00'))
    assert n(result['new']) == 0 and result['notification'] is None
    with pytest.raises(ValueError): store.run_stock_check('demo')


def test_run_stock_check_reports_changes_it_found(ready):
    # A reading that changed outside the app (e.g. a live feed) is found by the check and reported, not swallowed.
    store, ds, outbox = ready
    with store.connection() as db:
        db.execute("INSERT INTO stock_overrides VALUES(?,?,?,?)", (ds, SAFE, 0, '2026-10-09T00:00:00'))
    result = store.run_stock_check(ds)
    assert n(result['new']) == 1 and result['notification'] is not None
    assert eml_count(outbox) == 2


def test_run_stock_check_is_idempotent_and_does_not_double_notify(ready):
    store, ds, outbox = ready
    store.run_stock_check(ds); store.run_stock_check(ds)
    assert eml_count(outbox) == 1


# ---------------------------------------------------------------- no database lock when analysis runs inside a sync transaction
def test_attaching_reference_on_dataset_with_transfers_does_not_lock(tmp_path):
    store, ds = build(tmp_path, tols=None)
    started = time.perf_counter()
    store.import_reference(ds, tolerance_bytes(TOLS), 'minmax.xlsx')
    store.import_reference(ds, bom_bytes([[10, 'd', 'OTHERPART', 2, 'ROH', 'N']]), 'bom.xlsx')
    elapsed = time.perf_counter() - started
    assert elapsed < 5, elapsed                                                          # a second connection would wait out the 30 s timeout
    assert store.stock_view(ds)['forecast']['summary']['predicted'] == 1


def test_stock_view_and_edits_do_not_lock_with_cold_analysis_cache(ready):
    store, ds, _ = ready
    with store.connection() as db: db.execute('DELETE FROM analysis_cache WHERE dataset=?', (ds,))
    started = time.perf_counter()
    store.stock_view(ds)
    with store.connection() as db: db.execute('DELETE FROM analysis_cache WHERE dataset=?', (ds,))
    store.set_available(ds, SAFE, 30)
    store.update_stock_settings(ds, {'lead_hours_stocked': 4})
    assert time.perf_counter() - started < 5


def test_analysis_cache_is_populated_by_stock_view(ready):
    store, ds, _ = ready
    with store.connection() as db: db.execute('DELETE FROM analysis_cache WHERE dataset=?', (ds,))
    store.stock_view(ds)
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM analysis_cache WHERE dataset=?', (ds,)).fetchone()[0] == 1


def test_new_tables_exist_and_source_records_untouched(ready):
    store, ds, _ = ready
    with store.connection() as db:
        before = [r['payload'] for r in db.execute('SELECT payload FROM records WHERE dataset=? ORDER BY sheet,row_number', (ds,))]
        tables = {r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'stock_settings', 'stock_lead_classes'} <= tables
    store.update_stock_settings(ds, {'lead_hours_stocked': 5}); store.set_lead_class(ds, SAFE, 'fab'); store.set_available(ds, SAFE, 1)
    with store.connection() as db:
        assert before == [r['payload'] for r in db.execute('SELECT payload FROM records WHERE dataset=? ORDER BY sheet,row_number', (ds,))]


# ---------------------------------------------------------------- HTTP
def test_http_stock_view_exposes_forecast(ready):
    store, ds, _ = ready
    with serving(store) as base:
        status, view = call(base, f'/api/stock?dataset={ds}')
        assert status == 200 and view['forecast']['summary'] == dict(predicted=1, below_min=1, no_rate=1, ok=1)
        assert set(view['summary']) == {'positions', 'alerts', 'ok', 'no_tolerance', 'overridden'}


def test_http_settings_route(ready):
    store, ds, outbox = ready
    with serving(store) as base:
        status, body = call(base, '/api/stock/settings', {'dataset': ds, 'lead_hours_stocked': 48})
        assert status == 200 and body['settings']['lead_hours_stocked'] == 48.0 and n(body['new']) == 1
        status, body = call(base, '/api/stock/settings', {'dataset': ds, 'weights': {'history': 2}, 'divergence_pct': 25})
        assert status == 200 and body['settings']['weights']['history'] == 2.0 and body['settings']['divergence_pct'] == 25.0
        assert call(base, f'/api/stock?dataset={ds}')[1]['forecast']['settings']['lead_hours_stocked'] == 48.0
        for bad in ({'lead_hours_stocked': -1}, {'lead_hours_stocked': 'abc'}, {'bogus': 1}, {'weights': {'x': 1}}, {'lead_hours_fab': None}):
            status, body = call(base, '/api/stock/settings', dict(bad, dataset=ds))
            assert status == 400 and 'error' in body, bad
        assert call(base, '/api/stock/settings', {'dataset': 'demo', 'lead_hours_stocked': 5})[0] == 400
        assert call(base, '/api/stock/settings', {'dataset': 'nope', 'lead_hours_stocked': 5})[0] == 400
        assert call(base, '/api/stock/settings', {'dataset': ds, 'lead_hours_stocked': 5}, headers={'Content-Type': 'text/plain'})[0] == 415


def test_http_settings_do_not_treat_dataset_as_a_setting(ready):
    store, ds, _ = ready
    with serving(store) as base:
        status, body = call(base, '/api/stock/settings', {'dataset': ds})                # no changes: still valid
        assert status == 200 and body['settings'] == store.stock_settings(ds)


def test_http_lead_class_route(ready):
    store, ds, _ = ready
    with serving(store) as base:
        call(base, '/api/stock/settings', {'dataset': ds, 'lead_hours_fab': 48})
        status, body = call(base, '/api/stock/lead-class', {'dataset': ds, 'position': SAFE, 'lead_class': 'fab'})
        assert status == 200 and n(body['new']) == 1
        assert by_material(call(base, f'/api/stock?dataset={ds}')[1])['SAFE']['lead_class'] == 'fab'
        status, body = call(base, '/api/stock/lead-class', {'dataset': ds, 'position': SAFE, 'lead_class': None})
        assert status == 200 and n(body['resolved']) == 1
        for bad in ({'position': SAFE, 'lead_class': 'bogus'}, {'position': 'NOPE|1|X', 'lead_class': 'fab'}, {'lead_class': 'fab'},
                    {'position': SAFE, 'lead_class': 5}):
            status, body = call(base, '/api/stock/lead-class', dict(bad, dataset=ds))
            assert status == 400 and 'error' in body, bad
        assert call(base, '/api/stock/lead-class', {'dataset': 'demo', 'position': SAFE, 'lead_class': 'fab'})[0] == 400


def test_http_check_route(ready):
    store, ds, outbox = ready
    with serving(store) as base:
        status, body = call(base, '/api/stock/check', {'dataset': ds})
        assert status == 200 and body['checked_at'] and n(body['new']) == 0 and body['notification'] is None
        store.set_available(ds, SAFE, 1)                                                 # simulated edit already notified itself
        mails = eml_count(outbox)
        assert call(base, '/api/stock/check', {'dataset': ds})[1]['notification'] is None and eml_count(outbox) == mails
        assert call(base, '/api/stock/check', {'dataset': 'demo'})[0] == 400
        assert call(base, '/api/stock/check', {'dataset': 'missing'})[0] == 400
        assert call(base, '/api/stock/check', [1])[0] in (400, 413, 415)


def test_http_unknown_phase2_route_is_404_and_get_is_not_supported(ready):
    store, ds, _ = ready
    with serving(store) as base:
        assert call(base, '/api/stock/nothing', {'dataset': ds})[0] == 404
        assert call(base, f'/api/stock/check?dataset={ds}')[0] == 404                    # GET has no such route
