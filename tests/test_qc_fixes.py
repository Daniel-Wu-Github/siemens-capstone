"""Regressions for the 2026-10-09 QC findings (items 1-11). Synthetic fixtures only."""
import json
import ssl

import pytest

from siemens_capstone import alerts, store as store_module
from siemens_capstone.api import handler_for  # noqa: F401  (imported so a syntax error fails here too)
from qc_helpers import call, serving
from test_alerts import BIN, TOL, config, rows, store_with_data
from test_ingest import table_bytes


def payload(**kw):
    item = dict(material='PART-A', description='Bracket', supermarket='Silver', bin='A1', available=0, min=5, note=None)
    base = dict(new=[item], resolved=[], open_alerts=[item])
    base.update(kw)
    return alerts.payload('ds', 'at_or_below', None, base['new'], base['resolved'], base['open_alerts'])


class RecordingSMTP:
    calls = []

    def __init__(self, host, port, timeout=None, context=None):
        RecordingSMTP.calls.append(('connect', context))
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def starttls(self, context=None): RecordingSMTP.calls.append(('starttls', context))
    def login(self, user, password): RecordingSMTP.calls.append(('login', user))
    def send_message(self, msg): RecordingSMTP.calls.append(('send', msg['Subject']))


SMTP_CFG = dict(smtp_host='smtp.invalid', smtp_user='bot@x.test', smtp_password='HUNTER2', email_to=['wh@x.test'])


# 1 ---------------------------------------------------------------- duplicate rows / storage units
def test_storage_units_in_one_bin_are_summed_into_one_position():
    table = BIN + [['PART-A', 'Synthetic bracket', '401', 'A1', 7, 'PC'], ['PART-A', 'Synthetic bracket', '401', 'A1', 7, 'PC']]
    items = {p['key']: p for p in alerts.positions(rows(table), rows(TOL))}
    a = items['PART-A|401|A1']
    assert len(items) == 4 and a['available'] == 17 and '2 storage units summed' in a['note']  # identical duplicate counted once


def test_duplicate_bin_rows_open_one_alert(tmp_path):
    store = store_module.Store(tmp_path / 'd.sqlite3', alert_config=config(tmp_path))
    bins = BIN + [['PART-B', 'Synthetic bolt', '402', 'B1', 0, 'PC'], ['PART-B', 'Synthetic bolt', '402', 'B1', 0, 'XX']]
    wb = table_bytes({'HISTORICAL_PROD_DATA': [['salesorderOrdNr', 'productionorderSalesOrderLineItem', 'jobtraceObjid']],
                      'PLANNED_PROD_SCHEDULE': [['Sales Order', 'Line Item', 'Basic Start Date']],
                      'HISTORICAL_SM_REPLENISHMENTS': [['Transfer Order Number', 'Transfer order item', 'Material', 'Dest.Storage Bin', 'Dest.target quantity']],
                      'BIN_STATUS_EXAMPLE': bins})
    ds = store.import_workbook(wb, 'w.xlsx')['id']
    store.import_reference(ds, table_bytes({'Sheet3': TOL}), 'mm.xlsx')
    view = store.stock_view(ds)
    assert sum(a['material'] == 'PART-B' for a in view['open_alerts']) == 1


# 2 ---------------------------------------------------------------- TLS
def test_starttls_verifies_the_server_certificate(tmp_path):
    RecordingSMTP.calls.clear()
    out = alerts.deliver(payload(), config(tmp_path, **SMTP_CFG), tmp_path / 'ob', smtp_factory=RecordingSMTP)
    ctx = dict(RecordingSMTP.calls)['starttls']
    assert isinstance(ctx, ssl.SSLContext) and ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert [r['status'] for r in out['results']] == ['sent']


def test_implicit_tls_port_465_uses_smtp_ssl_with_verified_context(tmp_path):
    RecordingSMTP.calls.clear()
    cfg = config(tmp_path, **dict(SMTP_CFG, smtp_ssl=True, smtp_port=465))
    alerts.deliver(payload(), cfg, tmp_path / 'ob', smtp_factory=None, smtp_ssl_factory=RecordingSMTP)
    ctx = dict(RecordingSMTP.calls)['connect']
    assert ctx.verify_mode == ssl.CERT_REQUIRED and 'starttls' not in dict(RecordingSMTP.calls)
    assert alerts.config({'SMTP_PORT': '465'})['smtp_ssl'] is True


def test_password_is_never_sent_without_tls(tmp_path):
    RecordingSMTP.calls.clear()
    out = alerts.deliver(payload(), config(tmp_path, **dict(SMTP_CFG, smtp_starttls=False)), tmp_path / 'ob', smtp_factory=RecordingSMTP)
    assert 'login' not in dict(RecordingSMTP.calls)
    assert out['results'][0]['status'] == 'failed' and 'without TLS' in out['results'][0]['detail']


# 3 ---------------------------------------------------------------- stale Stock alerts page: see test_browser.py


# 4 ---------------------------------------------------------------- alerts that never resolved
def test_alert_resolves_when_tolerance_disappears_or_position_leaves_snapshot(tmp_path):
    store, ds = store_with_data(tmp_path)
    assert store.stock_view(ds)['summary']['alerts'] == 3
    # a newer Min/Max file without PART-B, and a dated snapshot without PART-D
    store.import_reference(ds, table_bytes({'Sheet3': [r for r in TOL if r[0] != 'PART-B']}), 'mm2.xlsx')
    snapshot = [r for r in BIN if r[0] != 'PART-D']
    store.import_reference(ds, table_bytes({'Sheet1': snapshot}), 'snap.xlsx', '2026-10-09')
    view = store.stock_view(ds)
    assert {a['material'] for a in view['open_alerts']} == {'PART-C'}
    with store.connection() as db:
        why = {json.loads(r['payload'])['material']: json.loads(r['payload'])['resolution']
               for r in db.execute("SELECT payload FROM stock_alerts WHERE status='Resolved'")}
    assert why == {'PART-B': 'minimum no longer set', 'PART-D': 'position not in current snapshot'}


# 5 / 6 ------------------------------------------------------------ every alert change is notified, once
def test_reference_import_announces_alert_changes(tmp_path):
    store, ds = store_with_data(tmp_path)
    notes = store.stock_view(ds)['notifications']
    assert [n['summary']['new'] for n in notes] == [3]


def test_reset_notifies_its_changes(tmp_path):
    store, ds = store_with_data(tmp_path)
    store.set_available(ds, 'PART-B|402|B1', 9)  # resolves a baseline alert
    r = store.reset_stock(ds)
    assert r['new'] == 1 and r['notification']['summary']['new'] == 1


def test_edit_notification_lists_only_the_edit_when_baseline_was_never_synced(tmp_path):
    store, ds = store_with_data(tmp_path)
    with store.connection() as db:  # simulate alerts that were never synced (e.g. a database from before this fix)
        db.execute('DELETE FROM stock_alerts'); db.execute('DELETE FROM notifications')
    r = store.set_available(ds, 'PART-A|401|A1', 0)
    assert r['new'] == 1 and r['notification']['summary']['new'] == 1 and 'PART-A' in r['notification']['subject']
    notes = store.stock_view(ds)['notifications']
    assert sorted(n['summary']['new'] for n in notes) == [1, 3]  # baseline announced separately from the edit


# 7 ---------------------------------------------------------------- errors are visible
def test_get_failure_returns_json_500_and_logs(tmp_path, capfd):
    store, ds = store_with_data(tmp_path)
    def boom(_): raise RuntimeError('disk on fire')
    store.stock_view = boom
    with serving(store) as base:
        status, body = call(base, f'/api/stock?dataset={ds}')
    assert status == 500 and 'server log' in body['error']
    assert 'disk on fire' in capfd.readouterr().err


def test_failure_detail_keeps_the_reason_but_never_secrets(tmp_path):
    class Refuse(RecordingSMTP):
        def login(self, user, password): raise RuntimeError(f'535 bad credentials for {user}:{password}')
    out = alerts.deliver(payload(), config(tmp_path, **SMTP_CFG), tmp_path / 'ob', smtp_factory=Refuse)
    detail = out['results'][0]['detail']
    assert '535 bad credentials' in detail and 'HUNTER2' not in detail and 'saved to outbox' in detail


def test_outbox_failure_is_not_reported_as_saved(tmp_path):
    blocker = tmp_path / 'file'; blocker.write_text('x')
    out = alerts.deliver(payload(), dict(config(tmp_path), outbox=str(blocker / 'ob')), tmp_path / 'unused')
    assert [r['status'] for r in out['results']] == ['failed'] and out['results'][0]['channel'] == 'outbox'


# 8 ---------------------------------------------------------------- outbox location and .eml policy
def test_default_outbox_is_in_the_repo_not_next_to_the_database(tmp_path):
    assert store_module.OUTBOX == store_module.Path(store_module.__file__).resolve().parents[2] / 'outputs' / 'outbox'


def test_no_eml_copy_when_smtp_succeeds(tmp_path):
    alerts.deliver(payload(), config(tmp_path, **SMTP_CFG), tmp_path / 'ob', smtp_factory=RecordingSMTP)
    assert not list((tmp_path / 'outbox').glob('*.eml'))


# 9 ---------------------------------------------------------------- confirmation: see test_browser.py


# 10 --------------------------------------------------------------- Teams card and subject
def test_teams_digest_lists_rows_resolved_and_more():
    many = [dict(material=f'P{i}', description='', supermarket='Silver', bin='A1', available=0, min=1, note=None) for i in range(20)]
    p = alerts.payload('ds', 'at_or_below', None, [], many[:2], many)
    body = alerts.teams_card(p, digest=many)['attachments'][0]['content']['body']
    texts = [b.get('text') for b in body]
    facts = [b for b in body if b['type'] == 'FactSet']
    assert 'Resolved' in texts and 'All open alerts' in texts and '+5 more' in texts
    assert len(facts) == 2 and len(facts[1]['facts']) == 15


def test_subject_follows_the_configured_rule_and_is_one_line():
    item = dict(material='PART-A', description='a\nb', supermarket='Silver\n', bin='A1', available=0, min=5, note=None)
    strict = alerts.payload('ds', 'below', None, [item], [], [item])
    msg = alerts.email_message(strict, 'a@x.test', ['b@x.test'])
    assert msg['Subject'].endswith('is below minimum') and '\n' not in msg['Subject']


def test_unknown_alert_rule_is_rejected_at_startup():
    with pytest.raises(ValueError): alerts.config({'ALERT_RULE': 'sometimes'})


# 11 --------------------------------------------------------------- Excel float part numbers
def test_integral_float_part_numbers_match_text():
    assert alerts.label(100200.0) == '100200' and alerts.label('100200.0') == '100200' and alerts.label('007') == '007'
