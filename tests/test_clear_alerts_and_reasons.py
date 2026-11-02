"""Clear-all-alerts and the no-rate reason; synthetic data, temporary DBs, test outbox."""
import pytest

from qc_helpers import call, eml_count, serving
from siemens_capstone import proactive
from test_phase2_store_api import build, by_material, open_by_position


def test_clear_all_acknowledges_below_min_only_and_leaves_stock_and_outbox_alone(tmp_path):
    store, ds = build(tmp_path)
    store.set_available(ds, 'SAFE|401|BS', 55)
    view = store.stock_view(ds)
    kinds = {a['material']: a['kind'] for a in view['open_alerts']}
    assert kinds == {'PRED': 'predicted', 'BM': 'below_min'}
    mails = eml_count(tmp_path / 'outbox')
    assert store.acknowledge_all_stock_alerts(ds) == dict(acknowledged=1)
    after = store.stock_view(ds)
    status = {a['material']: (a['kind'], a['status']) for a in after['open_alerts']}
    assert status == {'PRED': ('predicted', 'Open'), 'BM': ('below_min', 'Acknowledged')}
    assert by_material(after)['SAFE']['available'] == 55 and after['summary']['overridden'] == 1
    assert by_material(after)['BM']['status'] == 'Alert'      # the position itself is untouched
    assert eml_count(tmp_path / 'outbox') == mails           # nothing is sent
    assert store.acknowledge_all_stock_alerts(ds) == dict(acknowledged=0)   # idempotent


def test_cleared_alert_stays_cleared_while_in_breach_and_a_new_breach_alerts_again(tmp_path):
    store, ds = build(tmp_path)
    store.acknowledge_all_stock_alerts(ds)
    store.run_stock_check(ds)
    assert open_by_position(store.stock_view(ds))['BM|401|BB']['status'] == 'Acknowledged'
    store.set_available(ds, 'BM|401|BB', 50)                  # recovers: resolved
    assert 'BM|401|BB' not in open_by_position(store.stock_view(ds))
    store.set_available(ds, 'BM|401|BB', 1)                   # breaches again: a new Open alert
    assert open_by_position(store.stock_view(ds))['BM|401|BB']['status'] == 'Open'


def test_clear_all_endpoint(tmp_path):
    store, ds = build(tmp_path)
    with serving(store) as base:
        status, body = call(base, '/api/stock/ack-all', {'dataset': ds})
        assert (status, body) == (200, dict(acknowledged=1))
        assert call(base, '/api/stock/ack-all', {'dataset': 'missing'})[0] == 400


@pytest.mark.parametrize('inputs,bom,weights,reason', [
    (dict(history=None, schedule=None, observed=3.0), 'not_in_bom', dict(observed=0), 'not_in_bom'),
    (dict(history=None, schedule=None, observed=None), 'shared', dict(observed=0), 'shared_bom'),
    (dict(history=None, schedule=0.0, observed=None), 'ok', dict(observed=0), 'nothing_planned'),
    (dict(history=0.0, schedule=None, observed=None), 'not_in_bom', dict(observed=0), 'zero_rate'),
    (dict(history=None, schedule=None, observed=2.0), None, dict(observed=0), 'unweighted')])
def test_no_rate_reason(inputs, bom, weights, reason):
    assert proactive.no_rate_reason(inputs, bom, weights) == reason


def test_every_no_rate_position_has_a_reason_and_others_have_none(tmp_path):
    store, ds = build(tmp_path)
    for p in store.stock_view(ds)['positions']:
        assert (p['forecast'] == 'no_rate') == bool(p.get('no_rate_reason')), p['material']
        if p.get('no_rate_reason'): assert p['no_rate_reason'] in proactive.NO_RATE_REASONS
