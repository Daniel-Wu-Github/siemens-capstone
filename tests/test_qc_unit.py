"""Independent QC unit tests. Expected values come from the spec (DEMO_PLAN, docs/alerts.md, sponsor rules), not the code."""
import json
import logging
import math

import pytest

from siemens_capstone import alerts, analytics
from siemens_capstone.analytics import transfer_category, supermarket
from siemens_capstone.ingest import inspect_reference
from qc_helpers import (alert_cfg, analysis, bin_row, bom_bytes, tol_row, tolerance_bytes, transfer_row,
                        xlsx_bytes, BIN_HEADER)


def stock(material='P1', bin_='A1', available=10, typ='401'):
    return {'Material': material, 'Material Description': 'd', 'Storage Type': typ, 'Storage Bin': bin_,
            'Available stock': available, 'Base Unit of Measure': 'PC'}


def tol(part='P1', loc='A1', mn=5, mx=20, typ=401):
    return {'Part': part, 'Description': 'x', 'Type': typ, 'Location': loc, 'Min': mn, 'Max': mx}


def status(rows, tols, rule='at_or_below', overrides=None):
    return alerts.evaluate(alerts.positions(rows, tols, overrides), rule)


# ------------------------------------------------------------------ alert rule
def test_zero_minimum_alerts_only_when_empty():
    assert status([stock(available=0)], [tol(mn=0, mx=0)])[0]['status'] == 'Alert'
    assert status([stock(available=1)], [tol(mn=0, mx=0)])[0]['status'] == 'OK'


def test_equality_alerts_at_or_below_but_not_strict_below():
    assert status([stock(available=5)], [tol()])[0]['status'] == 'Alert'
    assert status([stock(available=5)], [tol()], 'below')[0]['status'] == 'OK'
    assert status([stock(available=4.999)], [tol()], 'below')[0]['status'] == 'Alert'
    assert status([stock(available=5.001)], [tol()])[0]['status'] == 'OK'


def test_strict_below_never_alerts_empty_zero_min_bin():
    assert status([stock(available=0)], [tol(mn=0, mx=0)], 'below')[0]['status'] == 'OK'


def test_unknown_rule_rejected():
    with pytest.raises(ValueError): alerts.evaluate([], 'sometimes')


def test_min_greater_than_max_still_evaluated_and_flagged():
    p = status([stock(available=4)], [tol(mn=40, mx=20)])[0]
    assert p['status'] == 'Alert' and p['note'] and 'min' in p['note'].lower()


def test_max_zero_with_positive_min_still_evaluated():
    assert status([stock(available=3)], [tol(mn=5, mx=0)])[0]['status'] == 'Alert'
    assert status([stock(available=30)], [tol(mn=5, mx=0)])[0]['status'] == 'OK'


def test_missing_tolerance_is_not_an_alert():
    p = status([stock()], [tol(part='OTHER')])[0]
    assert p['status'] == 'No tolerance' and p['min'] is None


def test_no_tolerance_file_at_all():
    assert status([stock(available=0)], None)[0]['status'] == 'No tolerance'
    assert status([stock(available=0)], [])[0]['status'] == 'No tolerance'


def test_ambiguous_duplicate_tolerance_is_not_guessed():
    p = status([stock(available=0)], [tol(mn=5, mx=10), tol(mn=1, mx=2)])[0]
    assert p['status'] != 'Alert' and p['min'] is None


def test_blank_minimum_in_tolerance_row_never_alerts():
    assert status([stock(available=0)], [tol(mn=None, mx=10)])[0]['status'] != 'Alert'


@pytest.mark.parametrize('bad', [None, '', 'abc', float('nan'), float('inf'), True])
def test_non_numeric_stock_is_no_stock_never_alert(bad):
    assert status([stock(available=bad)], [tol()])[0]['status'] == 'No stock'


def test_negative_stock_is_below_any_minimum():
    assert status([stock(available=-3)], [tol(mn=0, mx=0)])[0]['status'] == 'Alert'


def test_numeric_string_stock_and_tolerance_are_parsed():
    p = status([stock(available='3')], [tol(mn='5', mx='20')])[0]
    assert p['available'] == 3 and p['min'] == 5 and p['status'] == 'Alert'


def test_part_numbers_int_vs_string_match():
    assert status([stock(material=100200)], [tol(part='100200')])[0]['min'] == 5
    assert status([stock(material='100200')], [tol(part=100200)])[0]['min'] == 5


def test_part_number_float_from_excel_matches_integer_text():
    # Excel numeric cells can round-trip as 100200.0; the sponsor's part is the integer 100200.
    assert status([stock(material=100200.0)], [tol(part='100200')])[0]['min'] == 5


def test_bin_numeric_vs_text_match():
    assert status([stock(bin_=1001)], [tol(loc='1001')])[0]['min'] == 5


def test_whitespace_is_ignored_in_join():
    p = status([stock(material=' P1 ', bin_='A1 ')], [tol(part='P1', loc=' A1')])[0]
    assert p['min'] == 5 and p['material'] == 'P1'


def test_storage_type_mismatch_means_no_tolerance_and_match_when_int_vs_text():
    assert status([stock(typ='401')], [tol(typ=402)])[0]['min'] is None
    assert status([stock(typ='401')], [tol(typ=401)])[0]['min'] == 5
    assert status([stock(typ='')], [tol(typ=401)])[0]['min'] == 5
    assert status([stock(typ='401')], [tol(typ=None)])[0]['min'] == 5


def test_same_bin_in_two_storage_types_joins_each_to_its_own_tolerance():
    out = status([stock(typ='401', available=0), stock(typ='402', available=0)],
                 [tol(typ=401, mn=1, mx=9), tol(typ=402, mn=2, mx=9)])
    assert {p['storage_type']: p['min'] for p in out} == {'401': 1, '402': 2}


def test_rows_without_material_or_bin_are_skipped():
    assert alerts.positions([stock(material=''), stock(bin_=None)], [tol()]) == []


def test_override_replaces_source_and_is_flagged_without_mutating_input():
    rows = [stock(available=10)]
    p = alerts.positions(rows, [tol()], {'P1|401|A1': 0})[0]
    assert p['available'] == 0 and p['overridden'] and p['source_available'] == 10
    assert rows[0]['Available stock'] == 10


def test_supermarket_names_on_positions():
    assert alerts.positions([stock(typ='402')], [tol(typ=402)])[0]['supermarket'] == 'Green'
    assert alerts.positions([stock(typ='410')], [tol(typ=410)])[0]['supermarket'] == 'Type 410'


# ------------------------------------------------------------------ delivery
NEW = [dict(material='PART-A', description='Bracket <b>x</b>', supermarket='Silver', bin='A1', available=2, min=5, max=20, note=None)]


def payload(new=NEW, resolved=(), open_alerts=None, url=None):
    return alerts.payload('ds', 'at_or_below', '2026-10-01', list(new), list(resolved), open_alerts if open_alerts is not None else list(new), url)


class FakeSMTP:
    fail_at = None
    log = []
    def __init__(self, host, port, timeout=None):
        if self.fail_at == 'connect': raise ConnectionRefusedError(f'cannot reach {host} with pw=HUNTER2')
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def starttls(self, context=None):
        if self.fail_at == 'tls': raise OSError('tls failed HUNTER2')
    def login(self, user, password):
        if self.fail_at == 'login': raise RuntimeError(f'535 bad credentials {user}:{password}')
    def send_message(self, msg):
        if self.fail_at == 'send': raise OSError('550 rejected HUNTER2')
        FakeSMTP.log.append(msg)


SECRET_CFG = dict(smtp_host='smtp.invalid', smtp_user='bot@x.test', smtp_password='HUNTER2', email_to=['wh@x.test'],
                  teams_url='https://teams.invalid/hook?sig=TEAMSSECRET', webhook_url='https://hook.invalid/x?key=HOOKSECRET')


@pytest.mark.parametrize('stage', ['connect', 'tls', 'login', 'send'])
def test_smtp_failure_is_reported_not_raised_and_message_still_saved(tmp_path, stage, caplog, capfd):
    FakeSMTP.fail_at = stage
    cfg = alert_cfg(tmp_path / 'ob', **SECRET_CFG)
    with caplog.at_level(logging.DEBUG):
        out = alerts.deliver(payload(), cfg, tmp_path / 'ob', smtp_factory=FakeSMTP, poster=lambda u, b: None)
    FakeSMTP.fail_at = None
    email = [r for r in out['results'] if r['channel'] == 'email']
    assert email and email[0]['status'] == 'failed'
    assert list((tmp_path / 'ob').glob('*.eml')), 'failed email must still land in the outbox'
    blob = json.dumps(out) + caplog.text + ''.join(capfd.readouterr())
    for secret in ('HUNTER2', 'TEAMSSECRET', 'HOOKSECRET'):
        assert secret not in blob


def test_webhook_and_teams_failures_are_isolated_and_secret_free(tmp_path):
    seen = []
    def poster(url, body):
        seen.append(url)
        raise OSError(f'refused {url}')
    cfg = alert_cfg(tmp_path / 'ob', **dict(SECRET_CFG, smtp_host=''))
    out = alerts.deliver(payload(), cfg, tmp_path / 'ob', poster=poster)
    by = {r['channel']: r for r in out['results']}
    assert by['teams']['status'] == 'failed' and by['webhook']['status'] == 'failed'
    assert len(seen) == 2, 'a failing Teams post must not stop the webhook'
    blob = json.dumps(out)
    assert 'TEAMSSECRET' not in blob and 'HOOKSECRET' not in blob and 'hook.invalid' not in blob


def test_successful_smtp_sends_once_and_returns_no_password(tmp_path):
    FakeSMTP.log.clear()
    out = alerts.deliver(payload(), alert_cfg(tmp_path / 'ob', **SECRET_CFG), tmp_path / 'ob', smtp_factory=FakeSMTP, poster=lambda u, b: None)
    assert len(FakeSMTP.log) == 1 and [r['status'] for r in out['results'] if r['channel'] == 'email'] == ['sent']
    assert 'HUNTER2' not in json.dumps(out)


def test_without_smtp_email_is_saved_and_status_saved(tmp_path):
    out = alerts.deliver(payload(), alert_cfg(tmp_path / 'ob'), tmp_path / 'ob')
    assert [r['status'] for r in out['results']] == ['saved']
    assert len(list((tmp_path / 'ob').glob('*.eml'))) == 1


def test_each_channel_receives_same_payload_and_webhook_is_raw_json(tmp_path):
    posts = {}
    p = payload()
    alerts.deliver(p, alert_cfg(tmp_path / 'ob', teams_url='https://t.invalid/', webhook_url='https://w.invalid/'), tmp_path / 'ob',
                   poster=lambda u, b: posts.__setitem__(u, b))
    assert posts['https://w.invalid/'] == p
    assert json.dumps(posts['https://t.invalid/']).count(p['new'][0]['material']) >= 1


def test_payload_summary_counts_and_by_supermarket():
    a = [dict(NEW[0], supermarket='Silver'), dict(NEW[0], supermarket='Silver'), dict(NEW[0], supermarket='Green')]
    p = alerts.payload('ds', 'below', None, a[:1], a[1:2], a, None)
    assert p['summary'] == dict(open=3, new=1, resolved=1, by_supermarket={'Silver': 2, 'Green': 1})
    assert p['rule'] == 'available < min' and p['dataset'] == 'ds' and p['run_id'] and p['created_at']


def test_email_shape_subject_recipients_multipart_and_html_escaped():
    msg = alerts.email_message(payload(url='https://dash.invalid/'), 'from@x.test', ['a@x.test', 'b@x.test'])
    assert msg['To'] == 'a@x.test, b@x.test' and msg['From'] == 'from@x.test'
    assert 'PART-A' in msg['Subject'] and 'Silver' in msg['Subject']
    assert msg.is_multipart()
    plain = msg.get_body(('plain',)).get_content(); html = msg.get_body(('html',)).get_content()
    assert 'PART-A' in plain and 'https://dash.invalid/' in plain
    assert '<b>x</b>' not in html and '&lt;b&gt;' in html, 'descriptions must be HTML-escaped'
    assert 'Open dashboard' in html


def test_email_subject_variants_new_resolved_digest():
    assert 'more' in alerts.email_message(payload(new=NEW * 3), 'f@x', ['t@x'])['Subject']
    assert 'resolved' in alerts.email_message(payload(new=[], resolved=NEW, open_alerts=[]), 'f@x', ['t@x'])['Subject'].lower()
    assert 'digest' in alerts.email_message(payload(new=[], open_alerts=NEW), 'f@x', ['t@x'], digest=NEW)['Subject'].lower()


def test_digest_email_lists_every_open_alert():
    many = [dict(NEW[0], material=f'M{i}') for i in range(30)]
    msg = alerts.email_message(payload(new=[], open_alerts=many), 'f@x', ['t@x'], digest=many)
    text = msg.get_body(('plain',)).get_content()
    assert all(f'M{i} ' in text or f'M{i}\n' in text for i in range(30))


def test_teams_card_is_valid_adaptive_card_envelope():
    card = alerts.teams_card(payload(url='https://dash.invalid/'))
    assert card['type'] == 'message' and len(card['attachments']) == 1
    att = card['attachments'][0]
    assert att['contentType'] == 'application/vnd.microsoft.card.adaptive'
    c = att['content']
    assert c['type'] == 'AdaptiveCard' and c['version'] and c['$schema'].startswith('http') and c['body']
    assert any(b['type'] == 'TextBlock' for b in c['body'])
    assert c['actions'][0]['type'] == 'Action.OpenUrl' and c['actions'][0]['url'] == 'https://dash.invalid/'
    json.dumps(card, allow_nan=False)


def test_teams_card_without_dashboard_has_no_actions_and_caps_facts():
    many = [dict(NEW[0], material=f'M{i}') for i in range(40)]
    c = alerts.teams_card(payload(new=many))['attachments'][0]['content']
    assert 'actions' not in c
    facts = [b for b in c['body'] if b['type'] == 'FactSet'][0]['facts']
    assert 0 < len(facts) <= 15 and all(isinstance(f['title'], str) and isinstance(f['value'], str) for f in facts)


def test_public_config_never_contains_secrets():
    cfg = alerts.config({'SMTP_HOST': 'smtp.invalid', 'SMTP_PASSWORD': 'HUNTER2', 'ALERT_EMAIL_TO': 'a@x.test, b@x.test',
                         'TEAMS_WEBHOOK_URL': 'https://t.invalid/?sig=TEAMSSECRET', 'ALERT_WEBHOOK_URL': 'https://w.invalid/?k=HOOKSECRET'})
    blob = json.dumps(alerts.public_config(cfg))
    assert 'HUNTER2' not in blob and 'TEAMSSECRET' not in blob and 'HOOKSECRET' not in blob
    pub = alerts.public_config(cfg)
    assert pub['email']['configured'] and pub['teams']['configured'] and pub['webhook']['configured']
    assert cfg['email_to'] == ['a@x.test', 'b@x.test']


def test_config_defaults():
    cfg = alerts.config({})
    assert cfg['rule'] == 'at_or_below' and cfg['smtp_port'] == 587 and cfg['smtp_starttls'] is True and cfg['email_to'] == []
    assert alerts.config({'ALERT_RULE': 'below'})['rule'] == 'below'
    assert alerts.config({'SMTP_STARTTLS': '0'})['smtp_starttls'] is False


# ------------------------------------------------------------------ analytics
@pytest.mark.parametrize('src,dest,expected', [
    ('001', '401', 'replenishment'), ('011', '405', 'replenishment'), ('001', '414', 'replenishment'),
    ('401', '001', 'return_to_warehouse'), ('403', '001', 'return_to_warehouse'),
    ('401', '402', 'supermarket_transfer'), ('401', '401', 'supermarket_transfer'),
    ('002', '401', 'other'), ('001', '001', 'other'), ('001', '300', 'other'), ('011', '001', 'other'), ('', '401', 'other'),
])
def test_transfer_categories(src, dest, expected):
    assert transfer_category({'Source Storage Type': src, 'Dest. Storage Type': dest}) == expected


def test_transfer_category_numeric_codes_and_missing_source_column():
    assert transfer_category({'Source Storage Type': 1, 'Dest. Storage Type': 401}) == 'replenishment'
    assert transfer_category({'Source Storage Type': 11.0, 'Dest. Storage Type': '401'}) == 'replenishment'
    assert transfer_category({'Dest. Storage Type': '401'}) == 'replenishment'


@pytest.mark.parametrize('code,name', [('401', 'Silver'), ('402', 'Green'), ('403', 'Blue'), ('404', 'Purple'), ('405', 'Yellow'),
                                       ('406', 'Closing'), ('407', 'Brown'), ('408', 'Orange'), ('409', 'Type 409'), ('414', 'Type 414'), ('001', '001')])
def test_supermarket_names(code, name):
    assert supermarket(code) == name


def test_analysis_version_and_horizon_defaults():
    a = analysis([bin_row('P1', 'A1', 5)])
    assert analytics.ANALYSIS_VERSION == '2.1' and a['version'] == '2.1' and a['horizon_default_hours'] == 8


def test_category_counts_only_replenishment_feeds_rate():
    ts = [transfer_row(1, 'P1', 'A1', 10, 1), transfer_row(2, 'P1', 'A1', 10, 2), transfer_row(3, 'P1', 'A1', 10, 3),
          transfer_row(4, 'P1', 'A1', 999, 3, src='401', dest='001'), transfer_row(5, 'P1', 'A1', 999, 3, src='401', dest='402'),
          transfer_row(6, 'P1', 'A1', 999, 3, src='002', dest='401')]
    a = analysis([bin_row('P1', 'A1', 50)], [tol_row('P1', 'A1', 20, 100)], ts)
    assert a['transfers']['categories'] == dict(replenishment=3, return_to_warehouse=1, supermarket_transfer=1, other=1)
    p = a['inventory'][0]
    assert p['replen_qty_per_day'] == pytest.approx(10.0) and p['target_quantity_per_day'] == pytest.approx(10.0)


def test_reversed_time_transfers_are_excluded_from_categories():
    bad = transfer_row(9, 'P1', 'A1', 10, 3); bad['_created_at_local'] = '2026-10-03T00:00:00'; bad['_confirmed_at_local'] = '2026-10-02T00:00:00'
    a = analysis([bin_row('P1', 'A1', 50)], None, [transfer_row(1, 'P1', 'A1', 10, 1), bad])
    assert a['transfers']['categories']['replenishment'] == 1 and a['transfers']['invalid_time_order'] == 1


@pytest.mark.parametrize('mn,mx,flag', [
    # Definitions match the Stock alerts page (user decision 2026-10-09): min == max is valid, max 0 with min > 0 is "max not set".
    (5, 20, 'ok'), (0, 20, 'zero_min'), (0, 0, 'zero_min'), (20, 20, 'ok'), (30, 20, 'invalid'), (5, 0, 'max_not_set'),
])
def test_tolerance_flags(mn, mx, flag):
    p = analysis([bin_row('P1', 'A1', 7)], [tol_row('P1', 'A1', mn, mx)])['inventory'][0]
    assert p['tolerance_flag'] == flag and p['min'] == mn and p['max'] == mx


def test_tolerance_missing_flag_and_blank_values():
    assert analysis([bin_row('P1', 'A1', 7)], [tol_row('OTHER', 'A1', 1, 2)])['inventory'][0]['tolerance_flag'] == 'missing'
    assert analysis([bin_row('P1', 'A1', 7)])['inventory'][0]['tolerance_flag'] == 'missing'
    assert analysis([bin_row('P1', 'A1', 7)], [tol_row('P1', 'A1', None, 5)])['inventory'][0]['tolerance_flag'] == 'missing'


# min 0 is a real minimum: an empty bin is at its minimum (user decision 2026-10-09), as on the Stock alerts page.
@pytest.mark.parametrize('stock_,mn,expected', [(5, 5, True), (4, 5, True), (6, 5, False), (0, 0, True), (1, 0, False), (0, 1, True), (None, 5, False)])
def test_at_or_below_min_matches_stock_alert_rule(stock_, mn, expected):
    p = analysis([bin_row('P1', 'A1', stock_)], [tol_row('P1', 'A1', mn, 50)])['inventory'][0]
    assert p['at_or_below_min'] is expected


def test_fill_pct_and_none_when_max_zero():
    assert analysis([bin_row('P1', 'A1', 25)], [tol_row('P1', 'A1', 5, 100)])['inventory'][0]['fill_pct'] == pytest.approx(25.0)
    assert analysis([bin_row('P1', 'A1', 25)], [tol_row('P1', 'A1', 5, 0)])['inventory'][0]['fill_pct'] is None
    assert analysis([bin_row('P1', 'A1', 25)])['inventory'][0]['fill_pct'] is None


def test_inventory_supermarket_name_attached():
    inv = analysis([bin_row('P1', 'A1', 1, typ='402'), bin_row('P2', 'B1', 1, typ='411')])['inventory']
    assert {p['material']: p['supermarket'] for p in inv} == {'P1': 'Green', 'P2': 'Type 411'}


def completed(order, item, day, status_='70'):
    return {'productionorderOrdNr': order, 'productionorderStatus': status_, 'productionorderSalesOrderLineItem': item,
            'TransactionTime': f'2026-10-{day:02d}T08:00:00', 'productionorderProducedTime': f'2026-10-{day:02d}T08:00:00'}


BOM = [{'Item': 10, 'Part': 'US2:P1', 'Qty': 2}, {'Item': 10, 'Part': 'P2', 'Qty': 1}, {'Item': 20, 'Part': 'P2', 'Qty': 3}]


def test_bom_us2_prefix_normalised_and_status_codes():
    inv = analysis([bin_row('P1', 'A1', 5), bin_row('P9', 'A2', 5), bin_row('P2', 'A3', 5), bin_row('P2', 'A4', 5)], None, (), (), (), BOM)['inventory']
    st = {(p['material'], p['bin']): p['bom_status'] for p in inv}
    assert st[('P1', 'A1')] == 'ok' and st[('P9', 'A2')] == 'not_in_bom'
    assert st[('P2', 'A3')] == 'shared' and st[('P2', 'A4')] == 'shared'
    shared = [p for p in inv if p['bom_status'] == 'shared']
    assert all(p['bom_qty_per_day'] is None for p in shared)


def test_bom_stocked_part_with_us2_prefix_matches_unprefixed_bom_part():
    inv = analysis([bin_row('US2:P2', 'A1', 5)], None, (), (), (), BOM)['inventory']
    assert inv[0]['bom_status'] == 'ok'


def test_bom_qty_per_day_counts_completed_orders_once_over_window():
    prod = [completed('O1', '0010', 1), completed('O1', '0010', 1), completed('O2', '10', 2), completed('O3', '10', 3),
            completed('O4', '10', 3, status_='20'), completed('O5', '20', 3)]
    inv = analysis([bin_row('P1', 'A1', 50)], [tol_row('P1', 'A1', 20, 100)], (), prod, (), BOM)['inventory'][0]
    # 3 completed orders of item 10 (leading zeros ignored) x qty 2, window 3 days (Oct 1-3) = 2.0/day
    assert inv['bom_qty_per_day'] == pytest.approx(2.0)
    assert inv['days_to_min_bom'] == pytest.approx((50 - 20) / 2.0)


def test_bom_coverage_counts():
    prod = [completed('O1', '10', 1), completed('O2', '20', 2), completed('O3', '99', 3)]
    sched = [{'Sales Order': 'S1', 'Line Item': '0010', '_start_at_local': '2026-11-01T00:00:00'},
             {'Sales Order': 'S2', 'Line Item': '77', '_start_at_local': '2026-11-02T00:00:00'}]
    b = analysis([bin_row('P1', 'A1', 5)], None, (), prod, sched, BOM)['references']['bom']
    assert (b['line_items'], b['parts'], b['rows']) == (2, 2, 3)
    assert (b['production_orders_covered'], b['production_orders_total']) == (2, 3)
    assert (b['schedule_rows_covered'], b['schedule_rows_total']) == (1, 2)


def test_planned_qty_next_30_days_window_is_first_30_days_of_schedule():
    sched = [{'Sales Order': f'S{i}', 'Line Item': item, '_start_at_local': f'{d}T00:00:00'} for i, (item, d) in enumerate(
        [('10', '2026-11-01'), ('10', '2026-11-30'), ('10', '2026-12-01'), ('77', '2026-11-05')])]
    p = analysis([bin_row('P1', 'A1', 5)], None, (), (), sched, BOM)['inventory'][0]
    # Nov 1 and Nov 30 are inside days 0..29, Dec 1 is day 30 (outside); item 77 not in BOM.
    assert p['planned_qty_next_30d'] == pytest.approx(2 * 2)


def test_days_to_min_replen_zero_when_at_or_below_and_none_without_rate():
    ts = [transfer_row(i, 'P1', 'A1', 10, i) for i in (1, 2, 3)]
    inv = analysis([bin_row('P1', 'A1', 50), bin_row('P2', 'A2', 15), bin_row('P3', 'A3', 40)],
                   [tol_row('P1', 'A1', 20, 100), tol_row('P2', 'A2', 20, 100), tol_row('P3', 'A3', 20, 100)], ts)['inventory']
    by = {p['material']: p for p in inv}
    assert by['P1']['days_to_min_replen'] == pytest.approx(3.0)
    assert by['P3']['days_to_min_replen'] is None
    ts2 = ts + [transfer_row(10 + i, 'P2', 'A2', 10, i) for i in (1, 2, 3)]
    inv2 = analysis([bin_row('P2', 'A2', 15)], [tol_row('P2', 'A2', 20, 100)], ts2)['inventory'][0]
    assert inv2['days_to_min_replen'] == 0


def test_days_to_min_uses_zero_floor_when_tolerance_missing():
    ts = [transfer_row(i, 'P1', 'A1', 10, i) for i in (1, 2, 3)]
    assert analysis([bin_row('P1', 'A1', 50)], None, ts)['inventory'][0]['days_to_min_replen'] == pytest.approx(5.0)


def test_inventory_sort_order_below_min_then_days_then_material():
    ts = [transfer_row(i * 10 + d, m, b, 10, d) for i, (m, b) in enumerate([('A', 'a'), ('B', 'b'), ('C', 'c')]) for d in (1, 2, 3)]
    bins = [bin_row('A', 'a', 100), bin_row('B', 'b', 50), bin_row('C', 'c', 2), bin_row('D', 'd', 9)]
    tols = [tol_row(m, b, 5, 200) for m, b in (('A', 'a'), ('B', 'b'), ('C', 'c'), ('D', 'd'))]
    order = [p['material'] for p in analysis(bins, tols, ts)['inventory']]
    assert order == ['C', 'B', 'A', 'D']  # below-min first; then smallest days; then no-rate last


def test_snapshot_history_and_observed_depletion_ignores_receipts():
    snaps = [dict(as_of='2026-10-01', rows=[bin_row('P1', 'A1', 10)]), dict(as_of='2026-10-02', rows=[bin_row('P1', 'A1', 6)]),
             dict(as_of='2026-10-03', rows=[bin_row('P1', 'A1', 20)]), dict(as_of='2026-10-05', rows=[bin_row('P1', 'A1', 18)])]
    p = analysis([bin_row('P1', 'A1', 3)], None, snapshots=snaps)['inventory'][0]
    assert [h['as_of'] for h in p['snapshot_history']] == ['2026-10-01', '2026-10-02', '2026-10-03', '2026-10-05']
    assert p['observed_depletion_per_day'] == pytest.approx((4 + 1) / 2)  # drops 4/day and 2/2days; the +14 receipt is ignored


def test_observed_depletion_needs_two_snapshots():
    one = [dict(as_of='2026-10-01', rows=[bin_row('P1', 'A1', 10)])]
    assert analysis([bin_row('P1', 'A1', 3)], None, snapshots=one)['inventory'][0]['observed_depletion_per_day'] is None
    assert analysis([bin_row('P1', 'A1', 3)])['inventory'][0]['observed_depletion_per_day'] is None


def test_completed_order_structures_and_status_labels():
    prod = [completed('O1', '10', 1), completed('O2', '10', 1), completed('O3', '10', 8, '20')]
    p = analysis([bin_row('P1', 'A1', 5)], None, (), prod)['production']
    assert p['completed_orders_daily'] == [dict(period='2026-10-01', orders=2)]
    assert sum(w['orders'] for w in p['completed_orders_weekly']) == 2
    assert p['order_status_counts']['70']['label'] == 'Production complete' and p['order_status_counts']['20']['label'] == 'Released, not started'
    assert p['status_codes']['45'] == 'PIT'


def test_schedule_weekly_structure():
    sched = [{'Sales Order': 'S1', 'Line Item': '10', '_start_at_local': '2026-11-02T00:00:00'},
             {'Sales Order': 'S2', 'Line Item': '77', '_start_at_local': '2026-11-03T00:00:00'}]
    w = analysis([bin_row('P1', 'A1', 5)], None, (), (), sched, BOM)['schedule']['weekly']
    assert w == [dict(period='2026-11-02', lines=2, bom_lines=1)]


def test_tolerance_reference_summary_counts():
    tr = analysis([bin_row('P1', 'A1', 5), bin_row('P2', 'A2', 5), bin_row('P3', 'A3', 5), bin_row('P4', 'A4', 5)],
                  [tol_row('P1', 'A1', 5, 10), tol_row('P2', 'A2', 0, 0), tol_row('P3', 'A3', 9, 3)])['references']['tolerances']
    assert (tr['rows'], tr['matched_positions'], tr['zero_min'], tr['invalid']) == (3, 3, 1, 1)


# ------------------------------------------------------------------ ingest.inspect_reference
def test_inspect_detects_tolerances():
    r = inspect_reference(tolerance_bytes([['P1', 'd', 401, 'A1', 5, 20], ['P2', 'd', 402, 'B1', 0, 0]]), 'mm.xlsx')
    assert r['kind'] == 'tolerances' and r['rows'] == 2 and r['as_of'] is None and r['checksum']
    assert set(r) >= {'kind', 'checksum', 'rows', 'findings', 'as_of'}


def test_inspect_detects_bom_and_prefers_full_bom_sheet():
    r = inspect_reference(bom_bytes([[10, 'd', 'US2:P1', 2, 'ROH', 'N'], [10, 'd', 'P2', 1, 'ROH', 'N']]), 'bom.xlsx')
    assert r['kind'] == 'bom' and r['rows'] == 2


def test_inspect_bom_sheet_ordering_full_bom_wins_over_other_tolerance_like_sheet():
    content = xlsx_bytes({'Other': [['Part', 'Location', 'Min', 'Max'], ['P', 'L', 1, 2]],
                          'Full BOM': [['Item', 'Part', 'Qty'], [10, 'P1', 1], [10, 'P2', 1], [20, 'P3', 1]]})
    r = inspect_reference(content, 'x.xlsx')
    assert r['kind'] == 'bom' and r['rows'] == 3


def test_inspect_snapshot_requires_valid_date_for_xlsx_and_csv():
    content = xlsx_bytes({'S': [BIN_HEADER, ['P1', 'd', '401', 'A1', 5, 'PC']]})
    ok = inspect_reference(content, 's.xlsx', '2026-10-01')
    assert ok['kind'] == 'snapshot' and ok['as_of'] == '2026-10-01' and ok['rows'] == 1
    for bad in (None, '', '2026-13-01', '10/01/2026', '2026-1-1', 20261001):
        with pytest.raises(ValueError): inspect_reference(content, 's.xlsx', bad)
    csv = b'Material,Storage Bin,Available stock\nP1,A1,3\nP2,A2,4\n'
    r = inspect_reference(csv, 's.csv', '2026-10-02')
    assert r['kind'] == 'snapshot' and r['rows'] == 2
    with pytest.raises(ValueError): inspect_reference(csv, 's.csv')


def test_inspect_tolerances_ignore_as_of():
    assert inspect_reference(tolerance_bytes([['P1', 'd', 401, 'A1', 5, 20]]), 'mm.xlsx', '2026-10-01')['as_of'] is None


@pytest.mark.parametrize('content,name', [
    (b'', 'x.xlsx'), (b'not a zip', 'x.xlsx'), (xlsx_bytes({'S': [['A', 'B'], [1, 2]]}), 'x.xlsx'), (b'a,b\n1,2\n', 'x.csv'),
    (b'\xff\xfe\x00bad', 'x.csv'),
])
def test_inspect_unknown_or_unreadable_is_value_error(content, name):
    with pytest.raises(ValueError): inspect_reference(content, name, '2026-10-01')


def test_inspect_unrecognized_message():
    with pytest.raises(ValueError, match='Unrecognized reference file'):
        inspect_reference(xlsx_bytes({'S': [['A', 'B'], [1, 2]]}), 'x.xlsx')


def test_inspect_reports_duplicate_keys_and_bad_numbers_as_findings():
    r = inspect_reference(tolerance_bytes([['P1', 'd', 401, 'A1', 5, 20], ['P1', 'd', 401, 'A1', 6, 20], ['P2', 'd', 401, 'B1', 'x', 20]]), 'mm.xlsx')
    codes = {f['code'] for f in r['findings']}
    assert 'duplicate_key' in codes and 'invalid_number' in codes


def test_inspect_rejects_duplicate_headers_and_oversize():
    with pytest.raises(ValueError): inspect_reference(xlsx_bytes({'S': [['Part', 'Location', 'Min', 'Max', 'Min'], ['P', 'L', 1, 2, 3]]}), 'x.xlsx')
    with pytest.raises(ValueError): inspect_reference(b'0' * (32 * 1024 * 1024 + 1), 'x.xlsx')


def test_inspect_checksum_is_content_hash():
    a = tolerance_bytes([['P1', 'd', 401, 'A1', 5, 20]]); b = tolerance_bytes([['P1', 'd', 401, 'A1', 5, 21]])
    assert inspect_reference(a, 'a.xlsx')['checksum'] == inspect_reference(a, 'other-name.xlsx')['checksum'] != inspect_reference(b, 'a.xlsx')['checksum']
