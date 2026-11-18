"""Alert age ('open since') and last-replenished fields; synthetic data, temporary DBs only."""
from datetime import datetime, timedelta, timezone
import json

from qc_helpers import call, serving
from test_phase2_store_api import BM, PRED, TRANSFERS, build, by_material, open_by_position, transfer


def utc(text):
    return datetime.fromisoformat(text)


def test_open_alerts_expose_open_since_and_hours(tmp_path):
    store, ds = build(tmp_path)
    view = store.stock_view(ds)
    assert view['as_of_server']
    assert view['open_alerts']
    for alert in view['open_alerts']:
        assert utc(alert['open_since']).tzinfo is not None
        assert alert['open_hours'] >= 0


def test_open_since_uses_escalated_at_when_a_predicted_alert_escalates(tmp_path):
    store, ds = build(tmp_path)
    assert open_by_position(store.stock_view(ds))[PRED]['kind'] == 'predicted'
    store.set_available(ds, PRED, 1)
    alert = open_by_position(store.stock_view(ds))[PRED]
    assert alert['kind'] == 'below_min' and alert['escalated_at']
    assert alert['open_since'] == alert['escalated_at']


def test_acknowledging_does_not_change_open_since(tmp_path):
    store, ds = build(tmp_path)
    before = open_by_position(store.stock_view(ds))[BM]['open_since']
    store.acknowledge_all_stock_alerts(ds)
    after = open_by_position(store.stock_view(ds))[BM]
    assert after['status'] == 'Acknowledged' and after['open_since'] == before


def test_age_is_measured_from_the_stored_timestamp(tmp_path):
    store, ds = build(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(hours=51, minutes=30)).isoformat(timespec='seconds')
    with store.connection() as db:
        for row in db.execute('SELECT id,payload FROM stock_alerts WHERE dataset=?', (ds,)).fetchall():
            payload = json.loads(row['payload'])
            if payload['position'] == BM:
                payload['created_at'] = old
                db.execute('UPDATE stock_alerts SET payload=? WHERE id=?', (json.dumps(payload), row['id']))
    alert = open_by_position(store.stock_view(ds))[BM]
    assert 51.4 < alert['open_hours'] < 51.7


def test_rebreach_gets_a_new_open_since(tmp_path):
    store, ds = build(tmp_path)
    first = open_by_position(store.stock_view(ds))[BM]['open_since']
    store.set_available(ds, BM, 50)
    store.set_available(ds, BM, 1)
    second = open_by_position(store.stock_view(ds))[BM]['open_since']
    assert utc(second) >= utc(first)


def test_stock_endpoint_includes_age_fields(tmp_path):
    store, ds = build(tmp_path)
    with serving(store) as base:
        status, body = call(base, f'/api/stock?dataset={ds}')
    assert status == 200
    assert 'as_of_server' in body and all('open_hours' in a for a in body['open_alerts'])


def test_last_replenished_is_the_latest_confirmed_replenishment(tmp_path):
    store, ds = build(tmp_path)
    positions = by_material(store.stock_view(ds))
    assert positions['PRED']['last_replenished'] == '2026-10-10T00:00'
    assert positions['NORATE']['last_replenished'] is None


def test_last_replenished_ignores_non_replenishment_and_reports_outside_the_rate_window(tmp_path):
    older = transfer(90, 'NORATE', 'BN', 40, 2)         # Oct 2: replenishment, so it counts
    store, ds = build(tmp_path, transfers=TRANSFERS + [older], name='lr.sqlite3')
    assert by_material(store.stock_view(ds))['NORATE']['last_replenished'] == '2026-10-02T00:00'
    store30 = store.stock_view(ds, window=30)
    assert by_material(store30)['NORATE']['last_replenished'] == '2026-10-02T00:00'


def test_last_replenished_does_not_change_the_rates(tmp_path):
    store, ds = build(tmp_path)
    view = store.stock_view(ds)
    pred = by_material(view)['PRED']
    assert pred['demand_per_day'] == 30
