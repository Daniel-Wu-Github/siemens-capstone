"""Synthetic evidence for refill diagnostics and their HTTP contract."""
import csv
from io import StringIO
from urllib.request import urlopen

import pytest

from siemens_capstone import insights_parts
from qc_helpers import analysis, bin_row, transfer_row, full_workbook, make_store, tolerance_bytes, serving, call


def fixture(qtys=(40,), maximum=100, history=10, schedule=16, bom_status='ok'):
    position = dict(key='SYNTH|401|B', material='SYNTH', description='Synthetic', supermarket='Silver', storage_type='401', bin='B', min=10, max=maximum)
    inventory = dict(material='SYNTH', storage_type='401', bin='B', window_refills=[dict(date='2026-10-22', quantity=q) for q in qtys], replen_qty_per_day=history, planned_qty_next_30d=None if schedule is None else schedule * 30, bom_status=bom_status)
    return position, inventory


def metrics(**kwargs):
    return insights_parts.part_metrics(*fixture(**kwargs), insights_parts.DEFAULT_THRESHOLDS)


def test_window_refills_list_matches_counts():
    events = [transfer_row(i, 'SYNTH', 'B', qty, day) for i, qty, day in [(1, 30, 31), (2, 20, 15), (3, 10, 2), (4, 99, 1)]]
    row = analysis([bin_row('SYNTH', 'B', 80)], transfers=events)['inventory'][0]
    assert row['window_transfer_events'] == len(row['window_refills']) == 3
    assert row['window_refills'] == [dict(date='2026-10-02', quantity=10), dict(date='2026-10-15', quantity=20), dict(date='2026-10-31', quantity=30)]


@pytest.mark.parametrize('qty,flagged', [(40, True), (50, False), (60, False)])
def test_short_refill_threshold(qty, flagged):
    assert ('short_refills' in metrics(qtys=[qty] * 3)['flags']) == flagged


@pytest.mark.parametrize('count,flagged', [(1, False), (2, False), (3, True)])
def test_short_refill_needs_minimum_refill_count(count, flagged):
    row = metrics(qtys=[40] * count)
    assert row['median_refill_pct'] == 40  # the metric is still reported; only the flag waits for evidence
    assert ('short_refills' in row['flags']) == flagged


def test_min_refills_threshold_override_and_range():
    position, inventory = fixture(qtys=[40])
    one = insights_parts.part_metrics(position, inventory, dict(min_refills=1))
    assert 'short_refills' in one['flags']
    assert insights_parts.DEFAULT_THRESHOLDS['min_refills'] == 3
    for bad in (0, 101, 'x'):
        with pytest.raises(ValueError, match='min_refills'):
            insights_parts.validate_thresholds(dict(min_refills=bad))


@pytest.mark.parametrize('maximum', [None, 0, -1])
def test_missing_or_nonpositive_max(maximum):
    row = metrics(maximum=maximum)
    assert row['median_refill_pct'] is None and row['share_refills_95'] is None
    assert row['refills'][0]['pct_of_max'] is None
    assert 'short_refills' not in row['flags']


@pytest.mark.parametrize('count,flagged', [(8, False), (9, True)])
def test_frequent_refill_threshold(count, flagged):
    assert ('frequent_refills' in metrics(qtys=[60] * count)['flags']) == flagged


@pytest.mark.parametrize('schedule,flagged', [(14, False), (15, False), (16, True)])
def test_divergence_threshold(schedule, flagged):
    assert ('divergent' in metrics(schedule=schedule)['flags']) == flagged


def test_schedule_requires_unambiguous_bom():
    row = metrics(bom_status='shared')
    assert row['schedule_per_day'] is None and row['divergence_pct'] is None
    assert 'divergent' not in row['flags']


def test_days_under_min_pending_and_zero_history_no_divergence():
    row = metrics(history=0)
    assert row['days_under_min'] is None and row['days_under_min_status'] == 'pending'
    assert row['divergence_pct'] is None


def test_threshold_overrides_and_summary_count_positions_once():
    position, inventory = fixture(qtys=[40] * 9)
    report = insights_parts.parts_report([position], [inventory])
    assert report['summary']['flagged'] == 1
    assert all(report['summary'][flag] == 1 for flag in ('short_refills', 'frequent_refills', 'divergent'))
    assert report['summary']['positions'] == 1
    assert report['notes'][0] == 'Refill size uses the transfer target quantity, not a measured receipt.'
    assert insights_parts.parts_report([position], [inventory], dict(refill_size_pct=30, refills_30d=10, divergence_pct=70))['summary']['flagged'] == 0


def test_percent_summary_pools_refills_and_empty_report():
    p1, i1 = fixture(qtys=[40, 100]); p2, i2 = fixture(qtys=[100])
    p2.update(key='OTHER|401|C', material='OTHER', bin='C'); i2.update(material='OTHER', bin='C')
    report = insights_parts.parts_report([p1, p2], [i1, i2])
    assert report['summary']['median_refill_pct'] == 100
    assert report['summary']['share_refills_95'] == pytest.approx(2 / 3)
    assert insights_parts.parts_report([], [])['summary']['median_refill_pct'] is None


def test_csv_flattened_values_and_nulls():
    p, inv = fixture(maximum=None)
    report = insights_parts.parts_report([p], [inv])
    rows = list(csv.DictReader(StringIO(insights_parts.to_csv(report))))
    assert len(rows) == 1 and rows[0]['material'] == 'SYNTH'
    assert rows[0]['history_per_day'] == '10' and rows[0]['median_refill_pct'] == ''
    assert rows[0]['refills_30d'] == '1' and 'refills' not in rows[0]


@pytest.fixture
def stored(tmp_path):
    store = make_store(tmp_path)
    ds = store.import_workbook(full_workbook([['SYNTH', 'Synthetic', '401', 'B', 80, 'PC']]), 'synthetic.xlsx')['id']
    store.import_reference(ds, tolerance_bytes([['SYNTH', 'Synthetic', 401, 'B', 10, 100]]), 'tolerances.xlsx')
    return store, ds


def test_http_json_contract_and_csv_headers(stored):
    store, ds = stored
    with serving(store) as base:
        status, report = call(base, f'/api/insights/parts?dataset={ds}&refill_pct=25&refills=4&divergence=60&min_refills=2')
        assert status == 200
        assert {'dataset_id', 'window_days', 'window_start', 'window_end', 'thresholds', 'summary', 'notes', 'parts'} <= report.keys()
        assert report['thresholds'] == dict(refill_size_pct=25, refills_30d=4, divergence_pct=60, min_refills=2)
        assert report['dataset_id'] == ds and len(report['parts']) == 1
        with urlopen(base + f'/api/insights/parts.csv?dataset={ds}') as response:
            assert response.headers['Content-Type'] == 'text/csv; charset=utf-8'
            assert response.headers['Content-Disposition'] == 'attachment; filename="problem-parts.csv"'
            assert len(list(csv.DictReader(StringIO(response.read().decode())))) == 1


@pytest.mark.parametrize('query', ['refill_pct=-1', 'refill_pct=101', 'refills=101', 'divergence=1001', 'divergence=nan', 'refills=no', 'refill_pct=', 'min_refills=0'])
def test_http_bad_threshold_rejected(stored, query):
    store, ds = stored
    with serving(store) as base:
        assert call(base, f'/api/insights/parts?dataset={ds}&{query}')[0] == 400


def test_unknown_dataset_rejected(stored):
    store, _ = stored
    with serving(store) as base:
        status, body = call(base, '/api/insights/parts?dataset=missing')
        assert status == 400 and 'not found' in body['error'].lower()


def test_position_join_includes_storage_type_and_bin():
    position, inventory = fixture(qtys=[40])
    other_type = dict(position, key='SYNTH|402|B', storage_type='402')
    other_bin = dict(position, key='SYNTH|401|C', bin='C')
    rows = insights_parts.parts_report([position, other_type, other_bin], [inventory])['parts']
    assert [row['refills_30d'] for row in rows] == [1, 0, 0]
    assert rows[1]['history_per_day'] is None and rows[2]['flags'] == []


def test_even_median_and_95_percent_boundary():
    row = metrics(qtys=[20, 40, 95, 100])
    assert row['median_refill_pct'] == 67.5
    assert row['share_refills_95'] == 0.5


def test_http_window_metadata_matches_analytics(stored):
    store, ds = stored
    report = store.parts_insights(ds)
    assert report['window_days'] == 0 and report['window_start'] is None and report['window_end'] is None
