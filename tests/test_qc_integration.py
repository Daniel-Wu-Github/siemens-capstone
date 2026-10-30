"""Independent QC integration tests: temp DB + loopback HTTP server, synthetic fixtures, plus read-only real-file counts."""
import json
import logging
import sqlite3
from threading import Thread
import shutil

import pytest

from siemens_capstone import alerts
from siemens_capstone.ingest import inspect_reference
from siemens_capstone.store import Store
from qc_helpers import (REAL_BOM, REAL_FILES_PRESENT, REAL_MINMAX, REAL_PROD, Sink, alert_cfg, call, eml_count, full_workbook,
                        make_store, real_store, serving, tolerance_bytes)

BINS = [['PART-A', 'Bracket', '401', 'A1', 10, 'PC'], ['PART-B', 'Bolt', '402', 'B1', 0, 'PC'],
        ['PART-C', 'Lug', '403', 'C1', 5, 'PC'], ['PART-D', 'Cover', '404', 'D1', 4, 'PC'],
        ['PART-E', 'Pin', '405', 'E1', 50, 'PC']]
TOLS = [['PART-A', 'x', 401, 'A1', 5, 20], ['PART-B', 'x', 402, 'B1', 0, 0], ['PART-C', 'x', 403, 'C1', 5, 20],
        ['PART-D', 'x', 404, 'D1', 40, 20]]
A, B, C, D, E = ('PART-A|401|A1', 'PART-B|402|B1', 'PART-C|403|C1', 'PART-D|404|D1', 'PART-E|405|E1')


def post_file(base, content, query):
    return call(base, '/api/import?' + query, raw=content)


class Env:
    def __init__(self, tmp_path, **cfg):
        self.tmp = tmp_path
        self.store = make_store(tmp_path, **cfg)
        self.outbox = tmp_path / 'outbox'

    def start(self, stack, bins=BINS, tols=TOLS, load_page=True):
        self.base = stack.enter_context(serving(self.store))
        status, ds = post_file(self.base, full_workbook(bins), 'filename=w.xlsx')
        assert status == 201, ds
        self.ds = ds['id']
        if tols is not None:
            status, ref = post_file(self.base, tolerance_bytes(tols), f'filename=mm.xlsx&dataset={self.ds}')
            assert status == 201 and ref['kind'] == 'tolerances', ref
        if load_page: assert self.stock()[0] == 200
        # Every alert change is notified (QC fix 2026-10-09): attaching tolerances announces the baseline alerts once.
        # Clear that baseline email so the tests below count only the emails their own actions cause.
        self.baseline_emails = eml_count(self.outbox)
        for f in self.outbox.glob('*.eml'): f.unlink()
        return self

    def stock(self, ds=None): return call(self.base, f'/api/stock?dataset={ds or self.ds}')
    def post(self, path, **body): return call(self.base, path, dict(dataset=self.ds, **body))
    def edit(self, position, available): return self.post('/api/stock/available', position=position, available=available)
    def status(self, key):
        return next(p['status'] for p in self.stock()[1]['positions'] if p['key'] == key)


@pytest.fixture
def env(tmp_path):
    from contextlib import ExitStack
    with ExitStack() as stack:
        yield Env(tmp_path).start(stack)


# ------------------------------------------------------------------ core alert flow
def test_edit_before_page_load_notifies_only_the_edited_position(tmp_path):
    # docs/alerts.md: a notification carries "only the changes". Baseline alerts that already existed in the
    # source file are not changes caused by the edit, even if the page was never loaded.
    from contextlib import ExitStack
    with ExitStack() as stack:
        e = Env(tmp_path).start(stack, load_page=False)
        r = e.edit(A, 0)[1]
        assert r['new'] == 1, f"edit reported {r['new']} new alerts; only PART-A changed"


def test_baseline_and_listing(env):
    status, s = env.stock()
    assert status == 200 and s['tolerances_loaded'] is True
    assert s['summary']['positions'] == 5 and s['summary']['alerts'] == 3 and s['summary']['no_tolerance'] == 1
    assert {a['material'] for a in s['open_alerts']} == {'PART-B', 'PART-C', 'PART-D'}
    assert [p['status'] for p in s['positions'] if p['key'] == E] == ['No tolerance']
    assert env.baseline_emails == 1, 'attaching tolerances announces the baseline alerts in one notification'
    assert eml_count(env.outbox) == 0, 'loading the page again must not notify'
    notes = env.stock()[1]['notifications']
    assert len(notes) == 1 and notes[0]['summary']['new'] == 3


def test_get_stock_is_idempotent_and_does_not_notify(env):
    first = env.stock()[1]
    for _ in range(3): env.stock()
    again = env.stock()[1]
    assert [a['id'] for a in first['open_alerts']] == [a['id'] for a in again['open_alerts']]
    assert eml_count(env.outbox) == 0


def test_new_alert_exactly_one_notification_repeat_edit_none_recovery_resolved(env):
    status, r = env.edit(A, 5)
    assert status == 200 and r['new'] == 1 and r['resolved'] == 0 and r['notification']
    assert eml_count(env.outbox) == 1
    assert 'PART-A' in r['notification']['subject'] and r['notification']['summary']['new'] == 1
    assert r['notification']['summary']['open'] == 4
    # repeat edits while already in alert -> no notification
    for v in (4, 0, 5):
        s, r2 = env.edit(A, v)
        assert s == 200 and r2['new'] == 0 and r2['resolved'] == 0 and r2['notification'] is None
    assert eml_count(env.outbox) == 1
    # recovery -> one resolved notification
    s, r3 = env.edit(A, 6)
    assert r3['resolved'] == 1 and r3['new'] == 0 and r3['notification']
    assert 'resolved' in r3['notification']['subject'].lower()
    assert eml_count(env.outbox) == 2
    # repeat healthy edit -> nothing
    assert env.edit(A, 7)[1]['notification'] is None and eml_count(env.outbox) == 2
    # re-entering alert is a brand new alert
    assert env.edit(A, 1)[1]['new'] == 1 and eml_count(env.outbox) == 3


def test_equality_boundary_over_http(env):
    assert env.edit(A, 6)[1]['new'] == 0
    assert env.edit(A, 5)[1]['new'] == 1  # available == min alerts under the default rule


def test_zero_min_bin_alert_resolves_when_stocked(env):
    assert any(a['material'] == 'PART-B' for a in env.stock()[1]['open_alerts'])
    r = env.edit(B, 1)[1]
    assert r['resolved'] == 1 and eml_count(env.outbox) == 1
    assert env.edit(B, 0)[1]['new'] == 1


def test_edit_to_null_removes_override(env):
    env.edit(A, 0)
    assert env.status(A) == 'Alert'
    r = env.edit(A, None)[1]
    assert r['resolved'] == 1 and env.status(A) == 'OK'
    assert not any(p['overridden'] for p in env.stock()[1]['positions'])


def test_ack_then_further_edits_do_not_renotify_and_recovery_still_resolves(env):
    env.edit(A, 0)
    open_a = next(a for a in env.stock()[1]['open_alerts'] if a['material'] == 'PART-A')
    assert env.post('/api/stock/ack', id=open_a['id'])[0] == 200
    after = next(a for a in env.stock()[1]['open_alerts'] if a['material'] == 'PART-A')
    assert after['status'] == 'Acknowledged'
    n = eml_count(env.outbox)
    assert env.edit(A, 1)[1]['notification'] is None and eml_count(env.outbox) == n
    assert env.edit(A, 50)[1]['resolved'] == 1 and eml_count(env.outbox) == n + 1
    assert env.post('/api/stock/ack', id=open_a['id'])[0] == 400  # resolved alerts cannot be acked


def test_ack_invalid_ids_and_double_ack(env):
    assert env.post('/api/stock/ack', id='nope')[0] == 400
    assert env.post('/api/stock/ack')[0] == 400
    a = env.stock()[1]['open_alerts'][0]
    assert env.post('/api/stock/ack', id=a['id'])[0] == 200
    assert env.post('/api/stock/ack', id=a['id'])[0] == 400


def test_reset_clears_every_override_and_resolves_edit_driven_alerts(env):
    env.edit(A, 0); env.edit(E, 1)  # E has no tolerance
    env.edit(B, 9)  # resolves a baseline alert
    s = env.stock()[1]
    assert s['summary']['overridden'] == 3
    r = env.post('/api/stock/reset')[1]
    assert set(r) >= {'new', 'resolved'}
    s = env.stock()[1]
    assert s['summary']['overridden'] == 0
    assert {a['material'] for a in s['open_alerts']} == {'PART-B', 'PART-C', 'PART-D'}
    assert eml_count(env.outbox) == 3  # A new, B resolved, then the reset's own changes (A resolved, B reopened)
    assert r['new'] == 1 and r['resolved'] == 1 and r['notification']


def test_digest_lists_all_open_alerts_and_is_not_deduped(env):
    r = env.post('/api/stock/digest')[1]['notification']
    assert r['digest'] is True and r['summary']['open'] == 3
    for m in ('PART-B', 'PART-C', 'PART-D'): assert m in r['text']
    assert 'PART-A' not in r['text'] and 'PART-E' not in r['text']
    assert eml_count(env.outbox) == 1
    env.post('/api/stock/digest')
    assert eml_count(env.outbox) == 2
    eml = next(env.outbox.glob('*.eml')).read_text()
    assert 'Digest' in eml and 'text/html' in eml


def test_digest_with_zero_open_alerts_still_returns_a_notification(env):
    for k in (B, C, D): env.edit(k, 1000)
    n = eml_count(env.outbox)
    r = env.post('/api/stock/digest')[1]['notification']
    assert r['summary']['open'] == 0 and eml_count(env.outbox) == n + 1


def test_invalid_stock_edits_rejected_and_state_unchanged(env):
    for bad in (-1, 'abc', float('nan'), float('inf'), [1], {'a': 1}):
        status, body = env.edit(A, bad)
        assert status == 400, (bad, status, body)
    assert env.edit('NOPE|1|X', 1)[0] == 400
    assert env.edit(None, 1)[0] == 400
    assert call(env.base, '/api/stock/available', {'dataset': env.ds})[0] in (200, 400)  # missing available == clear, never 500
    assert env.status(A) == 'OK' and eml_count(env.outbox) == 0


def test_stock_endpoints_reject_synthetic_and_unknown_datasets(env):
    assert call(env.base, '/api/stock?dataset=demo')[0] == 400
    assert call(env.base, '/api/stock?dataset=missing')[0] == 400
    assert call(env.base, '/api/stock/available', {'dataset': 'demo', 'position': A, 'available': 1})[0] == 400
    assert call(env.base, '/api/stock/digest', {'dataset': 'missing'})[0] == 400


def test_http_guards_on_stock_routes(env):
    assert call(env.base, '/api/stock/available', {'dataset': env.ds, 'position': A, 'available': 0}, {'Origin': 'https://evil.example'})[0] == 403
    assert call(env.base, f'/api/stock?dataset={env.ds}', headers={'Host': 'evil.example'})[0] == 403
    status, _ = call(env.base, '/api/stock/available', raw=b'{"x":1}')
    assert status == 415
    assert call(env.base, '/api/stock/reset', [1, 2])[0] == 400


def test_source_records_are_never_modified(env):
    def snapshot():
        with sqlite3.connect(env.store.path) as db:
            return db.execute('SELECT dataset,sheet,row_number,payload FROM records ORDER BY 1,2,3').fetchall()
    before = snapshot()
    env.edit(A, 0); env.edit(B, 9); env.post('/api/stock/digest'); env.post('/api/stock/reset'); env.stock()
    assert snapshot() == before and len(before) == len(BINS)


def test_overrides_and_alerts_are_isolated_per_dataset(tmp_path):
    from contextlib import ExitStack
    with ExitStack() as stack:
        e = Env(tmp_path).start(stack)
        other_bins = [r[:4] + [99] + r[5:] for r in BINS]  # different content -> different dataset id
        s, ds2 = post_file(e.base, full_workbook(other_bins), 'filename=w2.xlsx')
        ds2 = ds2['id']; assert ds2 != e.ds
        assert e.stock(ds2)[1]['tolerances_loaded'] is False
        assert e.stock(ds2)[1]['summary']['alerts'] == 0, 'tolerance attached to dataset 1 must not leak into dataset 2'
        post_file(e.base, tolerance_bytes(TOLS), f'filename=mm.xlsx&dataset={ds2}')
        e.edit(A, 0)
        s1, s2 = e.stock()[1], e.stock(ds2)[1]
        assert s1['summary']['overridden'] == 1 and s2['summary']['overridden'] == 0
        assert s2['summary']['alerts'] == 0 and s1['summary']['alerts'] == 4
        assert call(e.base, '/api/stock/reset', {'dataset': ds2})[0] == 200
        assert e.stock()[1]['summary']['overridden'] == 1
        # same position key may be overridden independently
        call(e.base, '/api/stock/available', {'dataset': ds2, 'position': A, 'available': 0})
        assert e.stock(ds2)[1]['summary']['alerts'] == 1 and e.stock()[1]['summary']['alerts'] == 4


def test_import_idempotent_and_reference_dedupe(env):
    s, again = post_file(env.base, full_workbook(BINS), 'filename=w.xlsx')
    assert s == 201 and again['id'] == env.ds
    post_file(env.base, tolerance_bytes(TOLS), f'filename=mm.xlsx&dataset={env.ds}')
    with sqlite3.connect(env.store.path) as db:
        n = db.execute('SELECT COUNT(*) FROM "references" WHERE dataset=? AND kind=\'tolerances\'', (env.ds,)).fetchone()[0]
    assert n == 1
    assert env.stock()[1]['summary']['alerts'] == 3


def test_attaching_new_tolerances_invalidates_analysis_cache_and_changes_alerts(env):
    insights = call(env.base, f'/api/insights?dataset={env.ds}')[1]
    assert insights['references']['tolerances']['rows'] == 4
    new = [r[:] for r in TOLS]; new[0][4] = 100  # PART-A min -> 100: now alerting
    post_file(env.base, tolerance_bytes(new), f'filename=mm2.xlsx&dataset={env.ds}')
    insights = call(env.base, f'/api/insights?dataset={env.ds}')[1]
    inv = {p['material']: p for p in insights['inventory']}
    assert inv['PART-A']['min'] == 100 and inv['PART-A']['at_or_below_min'] is True
    assert env.status(A) == 'Alert'


def test_stock_snapshot_reference_supersedes_bin_sheet_and_reports_date(env):
    csv = b'Material,Storage Type,Storage Bin,Available stock\nPART-A,401,A1,0\nPART-E,405,E1,70\n'
    assert post_file(env.base, csv, f'filename=s.csv&dataset={env.ds}&as_of=2026-10-05')[0] == 201
    s = env.stock()[1]
    assert s['snapshot_as_of'] == '2026-10-05'
    assert env.status(A) == 'Alert'


def test_duplicate_bin_rows_do_not_crash_alerts(tmp_path):
    from contextlib import ExitStack
    dup = BINS + [['PART-D', 'Cover', '404', 'D1', 4, 'PC']]
    with ExitStack() as stack:
        e = Env(tmp_path).start(stack, bins=dup)
        status, s = e.stock()
        assert status == 200, s
        keys = [p['key'] for p in s['positions']]
        assert len(keys) == len(set(keys)), 'one alert row per position key'
        assert e.edit(D, 1)[0] == 200


def test_rule_below_changes_equality_behaviour(tmp_path):
    from contextlib import ExitStack
    with ExitStack() as stack:
        e = Env(tmp_path, rule='below').start(stack)
        s = e.stock()[1]
        assert {a['material'] for a in s['open_alerts']} == {'PART-D'}
        assert s['channels']['rule_text'] == 'available < min'
        assert e.edit(A, 5)[1]['new'] == 0 and e.edit(A, 4.5)[1]['new'] == 1


def test_no_tolerance_file_means_no_alerts_and_flag_false(tmp_path):
    from contextlib import ExitStack
    with ExitStack() as stack:
        e = Env(tmp_path).start(stack, tols=None)
        s = e.stock()[1]
        assert s['tolerances_loaded'] is False and s['summary']['alerts'] == 0 and s['summary']['no_tolerance'] == 5
        assert e.edit(A, 0)[1]['new'] == 0 and eml_count(e.outbox) == 0


# ------------------------------------------------------------------ concurrency / persistence
def test_concurrent_identical_edits_open_exactly_one_alert_and_one_notification(env):
    results = []
    def work(): results.append(env.edit(A, 0))
    threads = [Thread(target=work) for _ in range(12)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert all(s == 200 for s, _ in results), [r for r in results if r[0] != 200]
    assert sum(r['new'] for _, r in results) == 1
    assert eml_count(env.outbox) == 1
    with sqlite3.connect(env.store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM stock_alerts WHERE position=? AND status != 'Resolved'", (A,)).fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM stock_overrides WHERE position=?', (A,)).fetchone()[0] == 1


def test_concurrent_alternating_edits_stay_consistent(env):
    results = []
    def work(i): results.append(env.edit(A, 0 if i % 2 == 0 else 100))
    threads = [Thread(target=work, args=(i,)) for i in range(20)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert all(s == 200 for s, _ in results)
    final_alert = env.status(A) == 'Alert'
    new, resolved = sum(r['new'] for _, r in results), sum(r['resolved'] for _, r in results)
    assert new - resolved == (1 if final_alert else 0), (new, resolved, final_alert)
    assert eml_count(env.outbox) == sum(1 for _, r in results if r['notification'])
    s = env.stock()[1]
    assert s['summary']['alerts'] == len(s['open_alerts']), 'open alert rows must equal positions in Alert'


def test_concurrent_edits_on_different_positions(env):
    results = []
    keys = [(A, 0), (E, 1), (B, 9), (C, 1), (D, 100)]
    threads = [Thread(target=lambda k=k, v=v: results.append(env.edit(k, v))) for k, v in keys]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert all(s == 200 for s, _ in results)
    s = env.stock()[1]
    assert s['summary']['alerts'] == len(s['open_alerts']) == 2 and {a['material'] for a in s['open_alerts']} == {'PART-A', 'PART-C'}  # C set to 1 stays below its min of 5
    assert s['summary']['overridden'] == 5


def test_concurrent_get_and_edit_do_not_error(env):
    out = []
    ts = [Thread(target=lambda: out.append(env.stock()[0])) for _ in range(10)] + [Thread(target=lambda i=i: out.append(env.edit(A, i)[0])) for i in range(10)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert set(out) == {200}


def test_restart_persists_overrides_alerts_and_does_not_renotify(tmp_path):
    from contextlib import ExitStack
    with ExitStack() as stack:
        e = Env(tmp_path).start(stack)
        e.edit(A, 0); e.edit(B, 9)
        before = e.stock()[1]; n = eml_count(e.outbox); ds = e.ds; path = e.store.path
    store2 = Store(path, alert_config=alert_cfg(tmp_path / 'outbox'))
    with serving(store2) as base:
        after = call(base, f'/api/stock?dataset={ds}')[1]
        assert after['summary'] == before['summary']
        assert {a['id'] for a in after['open_alerts']} == {a['id'] for a in before['open_alerts']}
        assert any(p['overridden'] for p in after['positions'])
        assert eml_count(tmp_path / 'outbox') == n
        assert len(after['notifications']) == len(before['notifications']) == 3  # baseline + two edits


# ------------------------------------------------------------------ channels and secrets
def test_teams_and_webhook_payload_shapes_one_post_per_event_and_failure_isolated(tmp_path):
    from contextlib import ExitStack
    teams, hook = Sink(200), Sink(500)
    try:
        with ExitStack() as stack:
            e = Env(tmp_path, teams_url=teams.url, webhook_url=hook.url, dashboard_url='http://127.0.0.1:1/dash').start(stack)
            teams.bodies.clear(); hook.bodies.clear()  # drop the baseline announcement
            r = e.edit(A, 0)[1]
            assert len(teams.bodies) == 1 and len(hook.bodies) == 1
            card = teams.bodies[0]
            assert card['type'] == 'message' and card['attachments'][0]['contentType'] == 'application/vnd.microsoft.card.adaptive'
            assert card['attachments'][0]['content']['type'] == 'AdaptiveCard'
            assert card['attachments'][0]['content']['actions'][0]['url'] == 'http://127.0.0.1:1/dash'
            raw = hook.bodies[0]
            assert raw['summary']['new'] == 1 and raw['new'][0]['material'] == 'PART-A' and raw['dashboard_url']
            by = {x['channel']: x['status'] for x in r['notification']['results']}
            assert by['teams'] == 'sent' and by['webhook'] == 'failed' and by['email'] == 'saved'
            assert eml_count(e.outbox) == 1, 'webhook failure must not lose the email'
            assert e.edit(A, 0)[1]['notification'] is None and len(teams.bodies) == 1
            e.post('/api/stock/digest'); assert len(teams.bodies) == 2
    finally:
        teams.close(); hook.close()


def test_secrets_never_leak_through_api_db_logs_or_stdout(tmp_path, caplog, capfd):
    from contextlib import ExitStack
    teams, hook = Sink(200), Sink(500)
    cfg = dict(smtp_host='127.0.0.1', smtp_port=1, smtp_user='bot@x.test', smtp_password='HUNTER2PW', email_to=['wh@x.test'],
               teams_url=teams.url, webhook_url=hook.url)
    try:
        with ExitStack() as stack, caplog.at_level(logging.DEBUG):
            e = Env(tmp_path, **cfg).start(stack)
            blobs = [json.dumps(e.edit(A, 0)[1]), json.dumps(e.stock()[1]), json.dumps(e.post('/api/stock/digest')[1]),
                     json.dumps(e.post('/api/stock/reset')[1])]
            blobs.append(json.dumps(call(e.base, f'/api/export?dataset={e.ds}')[1]))
            blobs.append(json.dumps(call(e.base, f'/api/state?dataset={e.ds}')[1]))
            blobs.append(json.dumps(call(e.base, f'/api/insights?dataset={e.ds}')[1]))
            blobs.append(json.dumps(call(e.base, '/api/datasets')[1]))
            status, html = call(e.base, '/')
            blobs.append(html.decode(errors='replace'))
            with sqlite3.connect(e.store.path) as db:
                for table in ('notifications', 'activity', 'stock_alerts', 'datasets'):
                    blobs.append(json.dumps([tuple(map(str, r)) for r in db.execute(f'SELECT * FROM {table}')]))
            emls = ''.join(p.read_text(errors='replace') for p in e.outbox.glob('*.eml'))
            blobs.append(emls)
            blobs.append(caplog.text + ''.join(capfd.readouterr()))
            email_result = json.loads(blobs[0])['notification']['results']
            assert any(r['channel'] == 'email' and r['status'] == 'failed' for r in email_result), email_result
            assert eml_count(e.outbox) >= 1, 'failed SMTP must fall back to the outbox'
        blob = '\n'.join(blobs)
        secrets = ['HUNTER2PW', 'TOPSECRETSIG', f'127.0.0.1:{teams.server.server_port}', f'127.0.0.1:{hook.server.server_port}']
        for s in secrets: assert s not in blob, s
    finally:
        teams.close(); hook.close()


def test_channel_status_is_exposed_without_urls(tmp_path):
    from contextlib import ExitStack
    with ExitStack() as stack:
        e = Env(tmp_path, smtp_host='smtp.invalid', email_to=['a@x.test'], teams_url='https://t.invalid/?sig=Z', webhook_url='https://w.invalid/?k=Z').start(stack)
        ch = e.stock()[1]['channels']
        assert ch['teams']['configured'] and ch['webhook']['configured'] and ch['email']['configured'] and ch['email']['recipients'] == ['a@x.test']
        assert 'sig=Z' not in json.dumps(ch) and 'k=Z' not in json.dumps(ch)


# ------------------------------------------------------------------ real data (read-only, aggregates only)
real = pytest.mark.skipif(not REAL_FILES_PRESENT, reason='Siemens files not present in data/')


@pytest.fixture(scope='module')
def real_env():
    store, ds, timings = real_store()
    return store, ds, timings


@real
def test_real_workbook_row_counts(real_env):
    store, ds, _ = real_env
    q = store.sources(ds)['quality']
    counts = {s['name']: s['rows'] for s in q['sheets']}
    print('REAL sheet rows:', counts)
    assert counts == {'HISTORICAL_PROD_DATA': 75576, 'PLANNED_PROD_SCHEDULE': 6362, 'HISTORICAL_SM_REPLENISHMENTS': 30972, 'BIN_STATUS_EXAMPLE': 2317}


@real
def test_real_positions_and_tolerance_keys_match_both_ways(real_env):
    store, ds, _ = real_env
    s = store.stock_view(ds)
    print('REAL stock summary:', s['summary'])
    assert s['summary']['positions'] == 2317
    with sqlite3.connect(store.path) as db:
        bins = {(alerts.label(r['Material']), alerts.label(r['Storage Bin']), alerts.label(r.get('Storage Type')))
                for r in (json.loads(x[0]) for x in db.execute("SELECT payload FROM records WHERE dataset=? AND sheet='BIN_STATUS_EXAMPLE'", (ds,)))}
    tol = inspect_reference(REAL_MINMAX.read_bytes(), 'mm.xlsx')['records']
    tols = {(alerts.label(t['Part']), alerts.label(t['Location']), alerts.label(t.get('Type'))) for t in tol}
    print('REAL keys: bins', len(bins), 'tolerances', len(tols), 'bin-not-in-tol', len(bins - tols), 'tol-not-in-bin', len(tols - bins))
    assert len(tol) == 2317 and len(bins) == 2317
    assert bins == tols, 'every Min/Max (Part, Location, Type) must match the bin snapshot in both directions'
    assert s['summary']['no_tolerance'] == 0
    assert tol and all(t.get('Type') is not None for t in tol)


@real
def test_real_open_alert_counts_at_or_below_and_strict(real_env, tmp_path):
    store, ds, _ = real_env
    s = store.stock_view(ds)
    open_ = s['open_alerts']
    pos = [p for p in s['positions'] if p['status'] == 'Alert']
    positive = [p for p in pos if p['min'] > 0]; zero = [p for p in pos if p['min'] == 0]
    print(f"REAL alerts (available<=min): total={len(pos)} positive_min={len(positive)} zero_min_empty={len(zero)} open_rows={len(open_)}")
    copy = tmp_path / 'copy.sqlite3'
    with sqlite3.connect(store.path) as src, sqlite3.connect(copy) as dst: src.backup(dst)
    strict = Store(copy, alert_config=alert_cfg(tmp_path / 'ob', rule='below')).stock_view(ds)
    print('REAL alerts (available<min):', strict['summary']['alerts'])
    assert (len(pos), len(positive), len(zero)) == (72, 14, 58)
    assert len([a for a in open_ if a.get('kind', 'below_min') == 'below_min']) == 72
    assert len(open_) == 105
    assert strict['summary']['alerts'] == 13


@real
def test_real_replenishment_category_and_bom_coverage(real_env):
    store, ds, _ = real_env
    ins = store.insights(ds)
    t, b = ins['transfers'], ins['references']['bom']
    print('REAL transfers: raw', t['source_rows'], 'valid', t['valid_events'], 'categories', t['categories'], 'reversed', t['invalid_time_order'])
    print('REAL bom:', {k: b[k] for k in ('rows', 'line_items', 'parts', 'production_orders_covered', 'production_orders_total')})
    assert t['categories']['replenishment'] == 22862
    assert (b['production_orders_covered'], b['production_orders_total']) == (5362, 7281)
    assert (b['rows'], b['line_items'], b['parts']) == (10159, 135, 1307)


@real
def test_real_tolerance_shape_counts(real_env):
    store, ds, _ = real_env
    ins = store.insights(ds)
    tr = ins['references']['tolerances']
    tol = inspect_reference(REAL_MINMAX.read_bytes(), 'mm.xlsx')['records']
    n = lambda v: float(v)
    shape = dict(min0=sum(n(t['Min']) == 0 for t in tol), min_pos=sum(n(t['Min']) > 0 for t in tol), max0=sum(n(t['Max']) == 0 for t in tol),
                 both0=sum(n(t['Min']) == 0 and n(t['Max']) == 0 for t in tol), min_gt_max=sum(n(t['Min']) > n(t['Max']) for t in tol))
    print('REAL tolerance shape:', shape, 'analytics:', tr, 'empty positions:', ins['inventory_summary']['empty_positions'])
    assert shape == dict(min0=1269, min_pos=1048, max0=68, both0=58, min_gt_max=11)
    assert tr['zero_min'] == 1269 and tr['matched_positions'] == 2317
    assert ins['inventory_summary']['empty_positions'] == 68
    # User decision 2026-10-09: the Stock alerts definitions are authoritative. Of the 11 min > max rows,
    # 10 have max 0 ("max not set") and 1 is a true conflict.
    assert tr['invalid'] == 1 and tr['max_not_set'] == 10
    assert sum(p['at_or_below_min'] for p in ins['inventory']) == 72, 'insights must count the same 72 alerts as Stock alerts'


@real
def test_real_source_records_unchanged_by_stock_operations(real_env):
    store, ds, _ = real_env
    with sqlite3.connect(store.path) as db:
        before = db.execute('SELECT COUNT(*), SUM(LENGTH(payload)) FROM records WHERE dataset=?', (ds,)).fetchone()
    pos = store.stock_view(ds)['positions'][0]['key']
    store.set_available(ds, pos, 12345); store.reset_stock(ds)
    with sqlite3.connect(store.path) as db:
        after = db.execute('SELECT COUNT(*), SUM(LENGTH(payload)) FROM records WHERE dataset=?', (ds,)).fetchone()
    assert before == after and before[0] == 115227
