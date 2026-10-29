"""Table A7.2: integration tests IT-01 to IT-05.

These run stages against each other: files to cleaning, cleaning to model,
model to alert, model to dashboard, and alert to the Teams channel.
"""
import os
import time

import pytest

from siemens_capstone import alerts, pipeline

from conftest import requires_real_data
from helpers import bin_row, tolerance_row, transfer

pytestmark = pytest.mark.integration

DASHBOARD_URL = 'http://127.0.0.1:8765/#stock'


def _records(stock_rows, replen_rows=()):
    return {'HISTORICAL_SM_REPLENISHMENTS': list(replen_rows), 'HISTORICAL_PROD_DATA': [],
            'PLANNED_PROD_SCHEDULE': [], 'BIN_STATUS_EXAMPLE': list(stock_rows)}


def _model_with_breaches(breaches, healthy):
    """A model built through the real pipeline with a known number of bins below minimum."""
    stock, tolerances = [], []
    for i in range(breaches):
        stock.append(bin_row(f'LOW-{i:02}', f'Y-{i:02}', available=2, storage_type='405'))
        tolerances.append(tolerance_row(f'LOW-{i:02}', f'Y-{i:02}', 10, 100))
    for i in range(healthy):
        stock.append(bin_row(f'OK-{i:02}', f'G-{i:02}', available=80, storage_type='402'))
        tolerances.append(tolerance_row(f'OK-{i:02}', f'G-{i:02}', 10, 100))
    cleaned = pipeline.clean_all(_records(stock), tolerances)
    return pipeline.build_model(cleaned, tolerances)


@pytest.fixture
def warning_model():
    """IT-03's input: a model carrying exactly 3 parts in Warning."""
    return _model_with_breaches(breaches=3, healthy=5)


@requires_real_data()
def test_IT01_load_and_clean_all_files(real_cleaned):
    """Input: all Siemens files and the tolerance list.
    Expected: rows in = rows kept + rows removed, with a reason for each removed row."""
    assert real_cleaned['sources'], 'no sources were cleaned'

    for name, source in real_cleaned['sources'].items():
        assert source['rows_in'] == len(source['kept']) + len(source['removed']), (
            f'{name} does not reconcile: {source["rows_in"]} in, '
            f'{len(source["kept"])} kept, {len(source["removed"])} removed')
        for entry in source['removed']:
            assert entry.get('reason'), f'{name} removed a row without a reason'

    assert real_cleaned['balanced'] is True
    assert real_cleaned['rows_in'] == real_cleaned['rows_kept'] + real_cleaned['rows_removed']
    # The real extract genuinely contains rows worth removing.
    assert real_cleaned['rows_removed'] > 0


@requires_real_data()
def test_IT02_data_to_model(real_model):
    """Input: cleaned data from IT-01.
    Expected: every part gets a rate or is marked "not enough history"."""
    assert real_model['parts'], 'the model produced no parts'

    for part in real_model['parts']:
        has_rate = part['rate_per_day'] is not None
        marked = part['status'] == pipeline.NOT_ENOUGH_HISTORY
        assert has_rate != marked, (
            f"{part['part']} / {part['bin']} is neither rated nor marked "
            f"'not enough history' (status={part['status']}, rate={part['rate_per_day']})")

    assert real_model['modelled'] + real_model['not_enough_history'] == len(real_model['parts'])
    assert real_model['modelled'] > 0, 'no part had enough history to model'


def test_IT03_model_to_alert(warning_model):
    """Input: model output with 3 parts in Warning.
    Expected: alert lists those 3 parts with part, bin, and quantity."""
    warnings = [p for p in warning_model['parts'] if pipeline.classify(p, lead_hours=3) == pipeline.WARNING]
    assert len(warnings) == 3, 'fixture should present exactly 3 parts in Warning'

    rows = pipeline.alert_rows(warning_model, lead_hours=3)

    assert len(rows) == 3
    for row in rows:
        assert row['part'] and row['bin'], 'every alert row names its part and bin'
        assert row['quantity'] == 98, 'quantity brings the bin from 2 back up to its max of 100'
    assert {r['part'] for r in rows} == {p['part'] for p in warnings}


def test_IT04_model_to_dashboard():
    """Input: model output with 14 parts below minimum.
    Expected: dashboard shows 14 parts below minimum."""
    model = _model_with_breaches(breaches=14, healthy=6)

    view = pipeline.dashboard_view(model, lead_hours=3)

    assert view['summary']['below_minimum'] == 14
    assert view['summary']['positions'] == 20
    assert view['by_supermarket'] == {'Yellow': 14}
    # The dashboard sorts breaches to the top.
    assert all(row['below_minimum'] for row in view['rows'][:14])


def test_IT05_alert_to_teams(warning_model, tmp_path):
    """Input: alert from IT-03.
    Expected: shows up in the test channel within 1 minute and the link opens the dashboard.

    The channel is a local capture unless TEAMS_WEBHOOK_URL is set, in which
    case the card is posted to that real Teams channel.
    """
    rows = pipeline.alert_rows(warning_model, lead_hours=3)
    new = [dict(material=r['part'], description=f"{r['part']} description",
                supermarket=r['supermarket'], bin=r['bin'], available=r['available'],
                min=r['min'], note=None) for r in rows]

    live_url = os.environ.get('TEAMS_WEBHOOK_URL', '')
    cfg = alerts.config({'TEAMS_WEBHOOK_URL': live_url or 'https://teams.example.invalid/test-channel',
                         'ALERT_DASHBOARD_URL': DASHBOARD_URL, 'ALERT_OUTBOX': str(tmp_path)})
    posted = []

    def capture(url, body, timeout=10):
        posted.append((url, body))

    payload = alerts.payload('demo', cfg['rule'], '2026-09-30', new, [], new, cfg['dashboard_url'])

    started = time.perf_counter()
    result = alerts.deliver(payload, cfg, tmp_path,
                            poster=alerts._post if live_url else capture)
    elapsed = time.perf_counter() - started

    assert elapsed < 60, f'delivery took {elapsed:.1f}s, over the one-minute bar'
    teams = next(r for r in result['results'] if r['channel'] == 'teams')
    assert teams['status'] == 'sent', teams['detail']

    if not live_url:
        assert len(posted) == 1, 'exactly one card reaches the channel'
        card = posted[0][1]['attachments'][0]['content']
        # The link in the card opens the dashboard.
        assert card['actions'][0]['url'] == DASHBOARD_URL
        facts = [f for block in card['body'] if block['type'] == 'FactSet' for f in block['facts']]
        assert {f['title'] for f in facts} >= {r['part'] for r in rows}
