"""Synthetic daily snapshot import and quiet CLI regression coverage."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from siemens_capstone.ingest import inspect_reference
from qc_helpers import eml_count, full_workbook, make_store, tolerance_bytes, xlsx_bytes

HEADER = ['Part', 'Description', 'Type', 'Location', 'Min', 'Max', 'Live Value']
ERROR = 'A daily bin-status file needs a date: name it Bin_Status_YYYY-MM-DD.xlsx or pick a snapshot date.'


def daily(value=3, minimum=10, maximum=100):
    return xlsx_bytes({'Daily': [HEADER, ['SYN-A', 'Synthetic', 401, 'BIN-A', minimum, maximum, value]]})


def setup_store(tmp_path):
    store = make_store(tmp_path)
    ds = store.import_workbook(full_workbook([['SYN-A', 'Synthetic', '401', 'BIN-A', 50, 'PC']]), 'synthetic.xlsx')['id']
    store.import_reference(ds, tolerance_bytes([['SYN-A', 'Synthetic', 401, 'BIN-A', 10, 100]]), 'tol.xlsx')
    return store, ds


def test_daily_status_header_is_detected_before_tolerances():
    r = inspect_reference(daily(), 'Bin_Status_2026-10-22.xlsx')
    assert (r['kind'], r['format'], r['as_of']) == ('snapshot', 'daily_status', '2026-10-22')


def test_rows_are_normalised_to_snapshot_schema():
    r = inspect_reference(daily(), 'Bin_Status_2026-10-22.xlsx')['records'][0]
    assert r == {'Material': 'SYN-A', 'Material Description': 'Synthetic', 'Storage Type': '401', 'Storage Bin': 'BIN-A', 'Available stock': 3}


def test_explicit_as_of_overrides_filename():
    assert inspect_reference(daily(), 'Bin_Status_2026-10-22.xlsx', '2026-10-23')['as_of'] == '2026-10-23'


@pytest.mark.parametrize('filename,as_of', [('daily.xlsx', None), ('Bin_Status_2026-02-30.xlsx', None), ('daily.xlsx', 'bad')])
def test_missing_date_is_rejected(filename, as_of):
    with pytest.raises(ValueError, match='^' + ERROR.replace('.', r'\.') + '$'):
        inspect_reference(daily(), filename, as_of)


def test_plain_tolerance_file_is_still_tolerances():
    assert inspect_reference(tolerance_bytes([['SYN-A', 'Synthetic', 401, 'BIN-A', 10, 100]]), 'tol.xlsx')['kind'] == 'tolerances'


def test_daily_status_drives_stock_alerts(tmp_path):
    store, ds = setup_store(tmp_path)
    result = store.import_reference(ds, daily(), 'Bin_Status_2026-10-22.xlsx')
    view = store.stock_view(ds)
    assert result['format'] == 'daily_status'
    assert view['positions'][0]['status'] == 'Alert'
    assert view['snapshot_as_of'] == '2026-10-22'
    assert len(store.sources(ds)['references']['snapshots']) == 1


@pytest.mark.parametrize('dates', [('14', '22'), ('22', '14')])
def test_latest_daily_file_wins(tmp_path, dates):
    store, ds = setup_store(tmp_path)
    for day in dates:
        store.import_reference(ds, daily(int(day)), f'Bin_Status_2026-10-{day}.xlsx')
    view = store.stock_view(ds)
    assert view['snapshot_as_of'] == '2026-10-22'
    assert view['positions'][0]['available'] == 22


def test_quiet_import_syncs_but_does_not_notify(tmp_path):
    store, ds = setup_store(tmp_path)
    before = eml_count(tmp_path / 'outbox')
    result = store.import_reference(ds, daily(), 'Bin_Status_2026-10-22.xlsx', announce=False)
    assert result['notified'] is False
    view = store.stock_view(ds)
    assert view['positions'][0]['status'] == 'Alert'
    assert eml_count(tmp_path / 'outbox') == before
    store.set_available(ds, view['positions'][0]['key'], 50)
    assert eml_count(tmp_path / 'outbox') == before + 1


@pytest.mark.parametrize('minimum,count', [(11, 1), (10, 0)])
def test_tolerance_mismatch_finding(tmp_path, minimum, count):
    store, ds = setup_store(tmp_path)
    r = store.import_reference(ds, daily(minimum=minimum), 'Bin_Status_2026-10-22.xlsx')
    findings = [f for f in r['findings'] if f['code'] == 'tolerance_mismatch']
    assert len(findings) == count
    if count:
        assert findings[0] == dict(code='tolerance_mismatch', severity='warning', sheet='Daily', count=1, message='Min/Max differ from the tolerance file; the tolerance file is used.')
    assert store.stock_view(ds)['positions'][0]['min'] == 10


def test_cli_attach_to_existing_dataset_quiet_no_serve(tmp_path):
    store, ds = setup_store(tmp_path)
    path = tmp_path / 'Bin_Status_2026-10-22.xlsx'
    path.write_bytes(daily())
    env = {k: v for k, v in os.environ.items() if not k.startswith(('SMTP_', 'TEAMS_', 'WEBHOOK_', 'ALERT_'))}
    env['ALERT_OUTBOX'] = str(tmp_path / 'cli-outbox')
    result = subprocess.run([sys.executable, 'server.py', '--db', str(store.path), '--dataset', ds, '--quiet', '--no-serve', '--attach', str(path)], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert store.stock_view(ds)['snapshot_as_of'] == '2026-10-22'
    assert eml_count(tmp_path / 'cli-outbox') == 0


@pytest.mark.browser
def test_daily_status_counts_as_stock_snapshot_on_data_sources(tmp_path):
    from playwright.sync_api import sync_playwright, expect
    from qc_helpers import serving
    store, ds = setup_store(tmp_path)
    store.import_reference(ds, daily(), 'Bin_Status_2026-10-22.xlsx', announce=False)
    with serving(store) as base, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.goto(base + '/#data')
        tile = page.locator('.strip > div').filter(has_text='Stock snapshots')
        expect(tile.locator('b')).to_have_text('1')
        expect(page.get_by_text('2026-10-22 (1 row)')).to_be_visible()
        browser.close()
