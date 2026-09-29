from datetime import datetime
from io import BytesIO

import pytest
from openpyxl import Workbook

from siemens_capstone.ingest import inspect_workbook, excel_datetime
from siemens_capstone.store import Store


def workbook_bytes():
    wb = Workbook(); wb.remove(wb.active)
    for name, rows in {
        'HISTORICAL_PROD_DATA': [['salesorderOrdNr', 'productionorderSalesOrderLineItem', 'jobtraceObjid'], ['SO1', '0010', 'J1']],
        'PLANNED_PROD_SCHEDULE': [['Sales Order', 'Line Item', 'Basic Start Date'], ['SO1', '0010', datetime(2026, 9, 29)], ['SO2', '20', datetime(2026, 10, 1)]],
        'HISTORICAL_SM_REPLENISHMENTS': [['Transfer Order Number', 'Transfer order item', 'Material', 'Dest.Storage Bin', 'Dest.target quantity', 'Creation Date', 'Creation time', 'Confirmation Date', 'Confirmation time'], ['T1', '1', 'DEMO-X', 'A-1', -2, datetime(2026, 9, 29), 0.5, datetime(2026, 9, 28), 0.5]],
        'BIN_STATUS_EXAMPLE': [['Material', 'Storage Bin', 'Available stock', 'Base Unit of Measure'], ['DEMO-X', 'A-1', 10, 'PC'], ['DEMO-X', 'A-1', 10, 'PC']],
    }.items():
        ws = wb.create_sheet(name)
        for row in rows: ws.append(row)
    out = BytesIO(); wb.save(out); return out.getvalue()


def test_excel_date_systems_and_fractional_time():
    assert excel_datetime(1, 0.5, '1900') == '1900-01-01T12:00:00'
    assert excel_datetime(1, 0, '1904') == '1904-01-02T00:00:00'
    with pytest.raises(ValueError): excel_datetime('invalid', 0, '1900')


def test_inspection_reports_duplicates_bad_quantity_and_join_gaps():
    result = inspect_workbook(workbook_bytes(), 'synthetic.xlsx')
    assert result['total_rows'] == 6
    assert result['forecast_ready'] is False
    assert any(x['code'] == 'duplicate_key' for x in result['findings'])
    assert any(x['code'] == 'negative_quantity' for x in result['findings'])
    assert any(x['code'] == 'time_order' for x in result['findings'])
    join = next(j for j in result['joins'] if j['name'] == 'Schedule → production')
    assert join['matched'] == 1 and join['total'] == 2


def test_import_deduplicated_by_checksum_persists_rows(tmp_path):
    store = Store(tmp_path / 'test.sqlite3')
    first = store.import_workbook(workbook_bytes(), 'synthetic.xlsx')
    second = store.import_workbook(workbook_bytes(), 'synthetic.xlsx')
    assert first['id'] == second['id']
    state = Store(tmp_path / 'test.sqlite3').state(first['id'])
    assert state['dataset']['kind'] == 'workbook'
    assert state['quality']['total_rows'] == 6
    assert state['materials'] == []
    assert store.record_count(first['id']) == 6
    with pytest.raises(ValueError): store.evaluate(first['id'])


def test_missing_sheets_and_invalid_file_do_not_make_a_dataset(tmp_path):
    store = Store(tmp_path / 'test.sqlite3')
    before = len(store.datasets())
    with pytest.raises(ValueError): store.import_workbook(b'not an excel file', 'bad.xlsx')
    assert len(store.datasets()) == before
