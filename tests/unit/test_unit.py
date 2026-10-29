"""Table A7.1: unit tests UT-01 to UT-12.

Each test states the report's input and expected result, then checks it against
one function. No files, no database, no network.
"""
import pytest

from siemens_capstone import alerts, pipeline

from helpers import bin_row, tolerance_row, transfer

pytestmark = pytest.mark.unit


def test_UT01_remove_reverse_transfers():
    """Input: 5 rows, 2 from a supermarket back to the warehouse.
    Expected: 3 rows left, 2 reverse transfers removed."""
    rows = [
        transfer('001', '405', material='A'),   # warehouse -> Yellow
        transfer('405', '001', material='B'),   # reverse
        transfer('011', '403', material='C'),   # warehouse -> Blue
        transfer('405', '001', material='D'),   # reverse
        transfer('001', '402', material='E'),   # warehouse -> Green
    ]
    result = pipeline.clean_replenishments(rows)

    assert len(result['kept']) == 3
    assert len(result['removed']) == 2
    assert [r['row']['Material'] for r in result['removed']] == ['B', 'D']
    assert all(r['code'] == 'reverse_transfer' for r in result['removed'])
    assert all('supermarket back to warehouse' in r['reason'] for r in result['removed'])
    # Nothing is dropped silently.
    assert result['rows_in'] == len(result['kept']) + len(result['removed'])


def test_UT02_keep_only_install_and_distribution_stations():
    """Input: Main Install 3, Distribution 2.1, Shipping, Palletization.
    Expected: only Main Install 3 and Distribution 2.1 are kept."""
    rows = [{'stationDescr': name} for name in
            ('Main Install 3', 'Distribution 2.1', 'Shipping', 'Palletization')]
    result = pipeline.clean_stations(rows)

    assert [r['stationDescr'] for r in result['kept']] == ['Main Install 3', 'Distribution 2.1']
    assert [r['row']['stationDescr'] for r in result['removed']] == ['Shipping', 'Palletization']
    assert all(r['code'] == 'out_of_scope_station' for r in result['removed'])
    assert result['rows_in'] == len(result['kept']) + len(result['removed'])


def test_UT03_near_zero_job_times_are_flagged_and_excluded():
    """Input: job trace times of 0.03 and 25.8 minutes.
    Expected: 0.03 is flagged and left out of time averages."""
    rows = [{'JobTraceTime (MIN)': 0.03}, {'JobTraceTime (MIN)': 25.8}]
    result = pipeline.clean_job_times(rows)

    assert [r['JobTraceTime (MIN)'] for r in result['kept']] == [25.8]
    assert [r['row']['JobTraceTime (MIN)'] for r in result['removed']] == [0.03]
    assert result['removed'][0]['code'] == 'near_zero_time'
    # The average must come from the surviving row only.
    assert pipeline.average_job_minutes(result['kept']) == 25.8
    assert pipeline.average_job_minutes(rows) == pytest.approx(12.915)


def test_UT04_join_production_data_and_schedule():
    """Input: matching sales order and line item in both files.
    Expected: rows join correctly and unmatched rows are reported."""
    production = [
        {'salesorderOrdNr': '5001', 'productionorderSalesOrderLineItem': '10', 'jobtraceObjid': 'J1'},
        {'salesorderOrdNr': '5002', 'productionorderSalesOrderLineItem': '20', 'jobtraceObjid': 'J2'},
    ]
    schedule = [
        {'Sales Order': '5001', 'Line Item': '10', 'Basic Start Date': '2026-10-01'},
        {'Sales Order': '9999', 'Line Item': '90', 'Basic Start Date': '2026-10-02'},
    ]
    result = pipeline.join_production_schedule(production, schedule)

    assert len(result['matched']) == 1
    assert result['matched'][0]['key'] == ('5001', '10')
    assert result['matched'][0]['production'][0]['jobtraceObjid'] == 'J1'
    # Both sides report what did not join.
    assert [r['Sales Order'] for r in result['unmatched_schedule']] == ['9999']
    assert [r['jobtraceObjid'] for r in result['unmatched_production']] == ['J2']


def test_UT05_count_completed_orders():
    """Input: one order with steps at status 10, 11, 40, and 70.
    Expected: only the status 70 step counts as completed."""
    rows = [{'productionorderOrdNr': '7001', 'productionorderStatus': code}
            for code in ('10', '11', '40', '70')]
    result = pipeline.completed_orders(rows)

    assert result['completed_steps'] == 1          # only the status 70 row
    assert result['completed_orders'] == 1         # which completes the one order
    assert result['completed_order_numbers'] == ['7001']
    assert result['orders']['7001']['steps'] == 4  # all four steps still counted as steps


def test_UT06_check_tolerance_list():
    """Input: a part with min 50 and max 30, and a part with no min.
    Expected: both parts are flagged and skipped for alerts."""
    rows = [tolerance_row('PART-A', '405', minimum=50, maximum=30),
            tolerance_row('PART-B', '405', minimum=None, maximum=100)]
    result = pipeline.check_tolerances(rows)

    assert result['usable'] == []
    assert len(result['skipped']) == 2
    flagged = {r['part']: r for r in result['skipped']}
    assert flagged['PART-A']['flag'] == 'invalid'
    assert 'greater than maximum' in flagged['PART-A']['reason']
    assert flagged['PART-B']['flag'] == 'missing'
    assert 'not set' in flagged['PART-B']['reason']


def test_UT07_replenishment_rate():
    """Input: 6 replenishments while 120 products were built.
    Expected: rate is 0.05 per product."""
    assert pipeline.replenishment_rate(6, 120) == pytest.approx(0.05)

    with pytest.raises(ValueError):
        pipeline.replenishment_rate(6, 0)  # no production means no rate


def test_UT08_time_to_minimum():
    """Input: available 50, min 20, using 10 per hour. Expected: 3 hours."""
    assert pipeline.hours_to_minimum(50, 20, 10) == pytest.approx(3.0)

    assert pipeline.hours_to_minimum(20, 20, 10) == 0.0   # already at the minimum
    assert pipeline.hours_to_minimum(50, 20, 0) is None   # no use, no finite crossing


def test_UT09_alert_uses_lead_time():
    """Input: lead time 3 hours plus 1 hour buffer; time to minimum 3.5 vs 6 hours.
    Expected: alert for 3.5 hours, no alert for 6 hours."""
    assert pipeline.alert_due(3.5, lead_hours=3, buffer_hours=1) is True
    assert pipeline.alert_due(6, lead_hours=3, buffer_hours=1) is False
    # The boundary is inside the horizon.
    assert pipeline.alert_due(4.0, lead_hours=3, buffer_hours=1) is True


def test_UT10_part_in_two_supermarkets_alerts_once():
    """Input: yellow bin has 5 (min 10), green bin has 40 (min 10).
    Expected: one alert for the yellow bin only."""
    stock = [bin_row('PART-X', 'Y-01', available=5, storage_type='405'),
             bin_row('PART-X', 'G-01', available=40, storage_type='402')]
    tolerances = [tolerance_row('PART-X', 'Y-01', 10, 100),
                  tolerance_row('PART-X', 'G-01', 10, 100)]

    items = alerts.evaluate(alerts.positions(stock, tolerances))
    raised = [p for p in items if p['status'] == 'Alert']

    assert len(raised) == 1
    assert raised[0]['bin'] == 'Y-01'
    assert raised[0]['supermarket'] == 'Yellow'
    assert {p['supermarket'] for p in items} == {'Yellow', 'Green'}
    # The healthy green bin is reported, just not alerted.
    assert next(p for p in items if p['bin'] == 'G-01')['status'] == 'OK'


def test_UT11_uses_available_stock_not_total():
    """Input: total stock 30, available stock 18, min 20.
    Expected: status is Below Minimum."""
    stock = [bin_row('PART-Y', 'Y-02', available=18, total=30)]
    tolerances = [tolerance_row('PART-Y', 'Y-02', 20, 200)]

    position = alerts.positions(stock, tolerances)[0]

    assert position['available'] == 18          # available, not the 30 in Total Stock
    assert pipeline.stock_status(position['available'], position['min']) == 'Below Minimum'
    assert alerts.evaluate([position])[0]['status'] == 'Alert'


def test_UT12_suggested_quantity():
    """Input: max 200, available 12. Expected: suggests bringing 188."""
    assert pipeline.suggested_quantity(200, 12) == pytest.approx(188)

    assert pipeline.suggested_quantity(200, 200) == 0      # already full
    assert pipeline.suggested_quantity(200, 250) == 0      # never suggest a negative move
