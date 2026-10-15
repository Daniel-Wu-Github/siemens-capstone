import json

import pytest

from siemens_capstone import alerts
from siemens_capstone.store import Store
from test_ingest import table_bytes

BIN = [['Material', 'Material Description', 'Storage Type', 'Storage Bin', 'Available stock', 'Base Unit of Measure'],
       ['PART-A', 'Synthetic bracket', '401', 'A1', 10, 'PC'],
       ['PART-B', 'Synthetic bolt', '402', 'B1', 0, 'PC'],
       ['PART-C', 'Synthetic lug', '403', 'C1', 3, 'PC'],
       ['PART-D', 'Synthetic cover', '404', 'D1', 4, 'PC']]
TOL = [['Part', 'Description', 'Type', 'Location', 'Min', 'Max'],
       ['PART-A', 'x', 401, 'A1', 5, 20], ['PART-B', 'x', 402, 'B1', 0, 0],
       ['PART-C', 'x', 403, 'C1', 5, 0], ['PART-D', 'x', 404, 'D1', 40, 20]]


def config(tmp_path, **extra):
    return dict(alerts.config({}), outbox=str(tmp_path / 'outbox'), **extra)


def store_with_data(tmp_path, **extra):
    store = Store(tmp_path / 'alerts.sqlite3', alert_config=config(tmp_path, **extra))
    wb = table_bytes({'HISTORICAL_PROD_DATA': [['salesorderOrdNr', 'productionorderSalesOrderLineItem', 'jobtraceObjid']],
                      'PLANNED_PROD_SCHEDULE': [['Sales Order', 'Line Item', 'Basic Start Date']],
                      'HISTORICAL_SM_REPLENISHMENTS': [['Transfer Order Number', 'Transfer order item', 'Material', 'Dest.Storage Bin', 'Dest.target quantity']],
                      'BIN_STATUS_EXAMPLE': BIN})
    ds = store.import_workbook(wb, 'synthetic.xlsx')['id']
    store.import_reference(ds, table_bytes({'Sheet3': TOL}), 'minmax.xlsx')
    return store, ds


def rows(table):
    return [dict(zip(table[0], r)) for r in table[1:]]


def test_rule_alerts_at_or_below_minimum_and_zero_minimum_means_empty():
    items = {p['material']: p for p in alerts.evaluate(alerts.positions(rows(BIN), rows(TOL)))}
    assert items['PART-A']['status'] == 'OK'
    assert items['PART-B']['status'] == 'Alert'          # min 0, empty
    assert items['PART-C']['status'] == 'Alert' and items['PART-C']['note'] == 'max not set'
    assert items['PART-D']['status'] == 'Alert' and items['PART-D']['note'] == 'check tolerance (min > max)'
    assert items['PART-A']['supermarket'] == 'Silver'
    strict = {p['material']: p['status'] for p in alerts.evaluate(alerts.positions(rows(BIN), rows(TOL)), 'below')}
    assert strict['PART-B'] == 'OK'                      # 0 is not strictly below 0
    edge = alerts.evaluate(alerts.positions(rows(BIN), rows(TOL), {'PART-A|401|A1': 5}))
    assert next(p for p in edge if p['material'] == 'PART-A')['status'] == 'Alert'
    missing = alerts.evaluate(alerts.positions(rows(BIN), []))
    assert {p['status'] for p in missing} == {'No tolerance'}
    with pytest.raises(ValueError): alerts.evaluate([], 'sometimes')


def test_simulated_edit_opens_alert_sends_email_once_and_recovery_resolves(tmp_path):
    store, ds = store_with_data(tmp_path)
    view = store.stock_view(ds)
    assert view['summary'] == dict(positions=4, alerts=3, ok=1, no_tolerance=0, overridden=0)
    # attaching tolerances announced the 3 baseline alerts once; loading the page adds nothing
    assert len(view['open_alerts']) == 3 and [n['summary']['new'] for n in view['notifications']] == [3]
    assert view['notification'] is None
    for f in (tmp_path / 'outbox').glob('*.eml'): f.unlink()

    result = store.set_available(ds, 'PART-A|401|A1', 2)
    assert result['new'] == 1 and result['notification']['results'][0] == dict(
        channel='email', status='saved', detail=result['notification']['results'][0]['detail'])
    mails = list((tmp_path / 'outbox').glob('*.eml'))
    assert len(mails) == 1 and 'PART-A' in mails[0].read_text()
    again = store.set_available(ds, 'PART-A|401|A1', 1)
    assert again['new'] == 0 and again['notification'] is None             # still the same open alert

    recovered = store.set_available(ds, 'PART-A|401|A1', None)
    assert recovered['resolved'] == 1
    view = store.stock_view(ds)
    assert view['summary']['overridden'] == 0 and view['summary']['alerts'] == 3
    with pytest.raises(ValueError): store.set_available(ds, 'PART-A|401|A1', -1)
    with pytest.raises(ValueError): store.set_available(ds, 'NOPE|1|X', 1)


def test_source_rows_are_never_modified(tmp_path):
    store, ds = store_with_data(tmp_path)
    with store.connection() as db:
        before = [r['payload'] for r in db.execute('SELECT payload FROM records WHERE dataset=? ORDER BY sheet,row_number', (ds,))]
    store.set_available(ds, 'PART-A|401|A1', 0)
    store.reset_stock(ds)
    with store.connection() as db:
        after = [r['payload'] for r in db.execute('SELECT payload FROM records WHERE dataset=? ORDER BY sheet,row_number', (ds,))]
    assert before == after


def test_smtp_teams_and_webhook_channels_use_the_same_payload_and_hide_secrets(tmp_path):
    sent, posts = [], []

    class FakeSMTP:
        def __init__(self, host, port, timeout): sent.append(('connect', host, port))
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, context=None): sent.append('tls')
        def login(self, user, password): sent.append(('login', user))
        def send_message(self, msg): sent.append(('msg', msg['To'], msg['Subject']))

    cfg = config(tmp_path, smtp_host='smtp.test', smtp_user='bot@test', smtp_password='secret-pw', email_to=['wh@test'],
                 teams_url='https://teams.invalid/hook?sig=SECRET', webhook_url='https://hook.invalid/x')
    p = alerts.payload('ds', 'at_or_below', None, [dict(material='PART-A', description='d', supermarket='Silver', bin='A1', available=2, min=5, note=None)], [], [])
    out = alerts.deliver(p, cfg, tmp_path / 'outbox', smtp_factory=FakeSMTP, poster=lambda url, body: posts.append((url, body)))
    assert ('msg', 'wh@test', out['subject']) in sent and 'tls' in sent
    assert [r['channel'] for r in out['results']] == ['email', 'teams', 'webhook'] and all(r['status'] == 'sent' for r in out['results'])
    card = posts[0][1]['attachments'][0]
    assert card['contentType'] == 'application/vnd.microsoft.card.adaptive' and card['content']['type'] == 'AdaptiveCard'
    assert posts[1][1]['summary']['new'] == 1
    public = json.dumps(alerts.public_config(cfg))
    assert 'secret-pw' not in public and 'SECRET' not in public and 'wh@test' in public

    def broken(url, body): raise TimeoutError()
    out = alerts.deliver(p, dict(cfg, smtp_host=''), tmp_path / 'outbox', poster=broken)
    assert {r['channel']: r['status'] for r in out['results']} == {'email': 'saved', 'teams': 'failed', 'webhook': 'failed'}
    assert 'SECRET' not in json.dumps(out)
