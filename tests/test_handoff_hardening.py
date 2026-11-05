"""Hardening before sponsor handoff: read-only GET, unreadable files rejected, port-in-use message, CSV safety."""
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from qc_helpers import call, eml_count, full_workbook, make_store, serving, tolerance_bytes, xlsx_bytes
from siemens_capstone.insights_parts import to_csv
from test_phase2_store_api import build

HEADER = ['Part', 'Description', 'Type', 'Location', 'Min', 'Max', 'Live Value']
ROOT = Path(__file__).resolve().parents[1]


def dump(store):
    with store.connection() as db:
        return '\n'.join(db.iterdump())


def test_stock_view_changes_nothing_and_sends_nothing(tmp_path, monkeypatch):
    store, ds = build(tmp_path)
    before, mails = dump(store), eml_count(tmp_path / 'outbox')
    monkeypatch.setattr(store, '_sync_changes', lambda *a: pytest.fail('GET must not sync alerts'))
    monkeypatch.setattr(store, '_announce', lambda *a: pytest.fail('GET must not notify'))
    with serving(store) as base:
        status, body = call(base, f'/api/stock?dataset={ds}')
    assert status == 200 and body['notification'] is None and body['open_alerts']
    assert dump(store) == before and eml_count(tmp_path / 'outbox') == mails


@pytest.mark.parametrize('rows', [
    [HEADER],                                                   # header only
    [HEADER, ['SYN-A', 'x', 401, 'BIN-A', 10, 100, 'n/a'], ['SYN-B', 'x', 401, 'BIN-B', 10, 100, None]]])  # no numbers at all
def test_unreadable_daily_file_is_rejected_and_keeps_alerts(tmp_path, rows):
    store = make_store(tmp_path)
    ds = store.import_workbook(full_workbook([['SYN-A', 'Synthetic', '401', 'BIN-A', 5, 'PC']]), 'synthetic.xlsx')['id']
    store.import_reference(ds, tolerance_bytes([['SYN-A', 'Synthetic', 401, 'BIN-A', 10, 100]]), 'tol.xlsx')
    assert [a['status'] for a in store.stock_view(ds)['open_alerts']] == ['Open']
    with pytest.raises(ValueError, match='No readable stock values'):
        store.import_reference(ds, xlsx_bytes({'Daily': rows}), 'Bin_Status_2026-10-30.xlsx')
    view = store.stock_view(ds)
    assert [a['status'] for a in view['open_alerts']] == ['Open']


def test_partly_readable_daily_file_still_imports(tmp_path):
    store = make_store(tmp_path)
    ds = store.import_workbook(full_workbook([['SYN-A', 'Synthetic', '401', 'BIN-A', 5, 'PC']]), 'synthetic.xlsx')['id']
    rows = [HEADER, ['SYN-A', 'x', 401, 'BIN-A', 10, 100, 7], ['SYN-B', 'x', 401, 'BIN-B', 10, 100, 'n/a']]
    result = store.import_reference(ds, xlsx_bytes({'Daily': rows}), 'Bin_Status_2026-10-30.xlsx')
    assert result['rows'] == 2 and any(f['code'] == 'invalid_number' for f in result['findings'])


@pytest.mark.parametrize('sheets', [{'Sheet1': [['a', 'b'], [1, 2]]}, {'Sheet1': []}])
def test_wrong_workbook_is_rejected_and_creates_no_dataset(tmp_path, sheets):
    store = make_store(tmp_path)
    with serving(store) as base:
        status, body = call(base, '/api/import?filename=wrong.xlsx', raw=xlsx_bytes(sheets))
        assert status == 400 and 'not the manufacturing workbook' in body['error'] and 'Nothing was imported' in body['error']
        assert call(base, '/api/datasets')[1] == []


def test_server_reports_a_busy_port_without_a_traceback(tmp_path):
    with socket.socket() as busy:
        busy.bind(('127.0.0.1', 0)); busy.listen()
        port = busy.getsockname()[1]
        out = subprocess.run([sys.executable, str(ROOT / 'server.py'), '--port', str(port), '--db', str(tmp_path / 'p.sqlite3')],
                             capture_output=True, text=True, timeout=30)
    assert out.returncode == 1 and 'Traceback' not in out.stderr
    assert f'Cannot listen on port {port}' in out.stderr and f'--port {port + 1}' in out.stderr


def test_csv_cells_that_look_like_formulas_are_neutralised():
    parts = [dict(key='k', material='=1+1', description='+SUM(1)', supermarket='@x', storage_type='401', bin='-2+3', flags=['short_refills'],
                  refills_30d=3, median_refill_pct=-5.0)]
    from siemens_capstone.insights_parts import CSV_FIELDS
    text = to_csv(dict(parts=[{**{k: None for k in CSV_FIELDS}, **p} for p in parts]))
    body = text.splitlines()[1]
    assert "'=1+1" in body and "'+SUM(1)" in body and "'@x" in body and "'-2+3" in body
    assert ',-5.0' in body                                          # real negative numbers stay numbers
