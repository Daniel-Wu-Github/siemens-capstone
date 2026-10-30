"""Hand-computable synthetic held-out rate checks."""
import json
import subprocess
import sys

import pytest

from siemens_capstone import backtest
from qc_helpers import bin_row, transfer_row, full_workbook, make_store


def event(n, material, qty, day):
    row = transfer_row(n, material, 'BIN', qty, 1)
    row['_created_at_local'] = row['_confirmed_at_local'] = day + 'T00:00:00'
    return row


def records(schedule=True):
    return {
        'BIN_STATUS_EXAMPLE': [bin_row('SYNTHETIC_PRIVATE_PART', 'BIN', 100)],
        'HISTORICAL_SM_REPLENISHMENTS': [
            event(1, 'SYNTHETIC_PRIVATE_PART', 999, '2026-08-01'),
            event(2, 'SYNTHETIC_PRIVATE_PART', 30, '2026-08-02'),
            event(3, 'SYNTHETIC_PRIVATE_PART', 30, '2026-08-31'),
            event(4, 'SYNTHETIC_PRIVATE_PART', 120, '2026-09-01'),
            event(5, 'SYNTHETIC_PRIVATE_PART', 60, '2026-09-30')],
        'PLANNED_PROD_SCHEDULE': [{'Sales Order': 'S1', 'Line Item': 'I1', '_start_at_local': '2026-09-10'}] if schedule else [],
        'HISTORICAL_PROD_DATA': [{'productionorderOrdNr': 'PO1', 'productionorderSalesOrderLineItem': 'I1',
                                  'productionorderStatus': '70', 'productionorderProducedTime': '2026-09-20'}],
    }


REFS = {'bom': [{'Item': 'I1', 'Part': 'SYNTHETIC_PRIVATE_PART', 'Qty': 120}]}


def test_holdout_training_boundaries_and_rates():
    report = backtest.evaluate(records(), references=REFS)
    assert report['training'] == {'start': '2026-08-02', 'end': '2026-08-31', 'days': 30}
    assert report['holdout'] == {'start': '2026-09-01', 'end': '2026-09-30', 'days': 30}
    metric = report['variants']['history']
    assert metric['n'] == 1
    assert metric['mae'] == 4
    assert metric['mean_bias'] == -4
    assert metric['median_absolute_pct_error'] == pytest.approx(200 / 3)
    assert metric['share_within_50pct'] == 0


def test_metrics_zero_actual_and_within_50_boundary():
    result = backtest.metrics([(2, 3), (4, 1), (0, 2), (0, 0)])
    assert result == {'n': 3, 'mae': 2, 'median_absolute_pct_error': 62.5,
                      'mean_bias': 0, 'share_within_50pct': .5}


def test_schedule_and_christian_variants():
    report = backtest.evaluate(records(), references=REFS)
    v = report['variants']
    assert v['schedule']['mae'] == 2  # schedule 4/day, actual 6
    assert v['blend_current']['mae'] == 2  # divergence override
    assert v['blend_no_override']['mae'] == 3
    assert v['blend_history_2to1']['mae'] == pytest.approx(10 / 3)
    assert v['history_only_when_both'] == v['history']
    assert report['common_subset']['variants'] == v


def test_schedule_skipped_and_foresight_separate():
    report = backtest.evaluate(records(False), references=REFS)
    assert report['schedule_status'] == 'schedule not testable on this extract'
    assert 'schedule' not in report['variants']
    assert 'blend_current' not in report['variants']
    assert report['perfect_foresight']['label'] == 'with perfect-foresight schedule'
    assert report['perfect_foresight']['variants']['blend_current']['mae'] == 2
    assert report['labels']['bom_actual'] == 'perfect-foresight upper bound'


def test_report_contains_only_aggregates():
    report = backtest.evaluate(records(), references=REFS)
    rendered = backtest.to_markdown(report)
    assert rendered.startswith('Validates the daily demand rate on held-out transfers. It does not test stockout prediction.')
    assert 'perfect-foresight upper bound' in rendered
    assert 'SYNTHETIC_PRIVATE_PART' not in rendered + json.dumps(report)
    assert 'BIN' not in rendered + json.dumps(report)


def test_invalid_conflicting_and_non_replenishment_events_use_analytics_rules():
    rows = records()
    rows['HISTORICAL_SM_REPLENISHMENTS'] += [event(6, 'SYNTHETIC_PRIVATE_PART', 900, '2026-10-01')]
    conflict = dict(rows['HISTORICAL_SM_REPLENISHMENTS'][-1], **{'Dest.target quantity': 901})
    rows['HISTORICAL_SM_REPLENISHMENTS'].append(conflict)
    other = event(7, 'SYNTHETIC_PRIVATE_PART', 900, '2026-10-02')
    other['Source Storage Type'] = '401'
    rows['HISTORICAL_SM_REPLENISHMENTS'].append(other)
    assert backtest.evaluate(rows, references=REFS)['holdout']['end'] == '2026-09-30'


def test_missing_events_rejected():
    with pytest.raises(ValueError, match='No valid replenishment'):
        backtest.evaluate({})


def test_cli_read_only_db_outputs(tmp_path):
    store = make_store(tmp_path)
    ds = store.import_workbook(full_workbook([['X', 'synthetic', '401', 'BIN', 10, 'PC']], transfers=[
        ['T1', '1', 'X', '001', '401', 'BIN', 30, '2026-09-30', 0, '2026-09-30', 0]]), 'synthetic.xlsx')['id']
    before = store.path.read_bytes()
    result = subprocess.run([sys.executable, 'scripts/backtest_rates.py', '--db', str(store.path), '--dataset', ds,
                             '--output-dir', str(tmp_path / 'reports')], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert store.path.read_bytes() == before
    assert (tmp_path / 'reports/backtest_2026-09-30.md').exists()
    assert json.loads((tmp_path / 'reports/backtest_2026-09-30.json').read_text())['variants']['history']['mae'] == 1


def test_cli_leaves_no_sqlite_side_files_next_to_the_database(tmp_path):
    store = make_store(tmp_path)
    ds = store.import_workbook(full_workbook([['X', 'synthetic', '401', 'BIN', 10, 'PC']], transfers=[
        ['T1', '1', 'X', '001', '401', 'BIN', 30, '2026-09-30', 0, '2026-09-30', 0]]), 'synthetic.xlsx')['id']
    siblings = lambda: sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(store.path.name))
    before = siblings()
    result = subprocess.run([sys.executable, 'scripts/backtest_rates.py', '--db', str(store.path), '--dataset', ds,
                             '--output-dir', str(tmp_path / 'reports')], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert siblings() == before  # a read-only open of a WAL database would leave -shm/-wal behind


def test_common_subset_excludes_positions_without_bom():
    rows = records()
    rows['BIN_STATUS_EXAMPLE'].append(bin_row('UNMAPPED_SYNTHETIC', 'BIN', 100))
    rows['HISTORICAL_SM_REPLENISHMENTS'].append(event(8, 'UNMAPPED_SYNTHETIC', 300, '2026-09-30'))
    report = backtest.evaluate(rows, references=REFS)
    assert report['variants']['history']['n'] == 2
    assert report['variants']['schedule']['n'] == 1
    assert report['common_subset']['positions'] == 1
    assert report['common_subset']['variants']['history']['mae'] == 4


def test_production_orders_deduplicate_and_use_final_completion_date():
    rows = records()
    row = rows['HISTORICAL_PROD_DATA'][0]
    rows['HISTORICAL_PROD_DATA'].append(dict(row))
    assert backtest.evaluate(rows, references=REFS)['variants']['bom_actual']['mae'] == 2
    rows['HISTORICAL_PROD_DATA'].append(dict(row, productionorderProducedTime='2026-10-01'))
    assert backtest.evaluate(rows, references=REFS)['variants']['bom_actual']['mae'] == 6


def test_schedule_outside_holdout_and_shared_parts_are_not_used():
    rows = records()
    rows['PLANNED_PROD_SCHEDULE'][0]['_start_at_local'] = '2026-10-01'
    assert backtest.evaluate(rows, references=REFS)['schedule_status'] == 'schedule not testable on this extract'
    rows = records()
    rows['BIN_STATUS_EXAMPLE'].append(bin_row('SYNTHETIC_PRIVATE_PART', 'OTHER_BIN', 100))
    report = backtest.evaluate(rows, references=REFS)
    assert report['variants']['schedule']['n'] == 0
    assert report['variants']['bom_actual']['n'] == 0


def test_common_subset_has_identical_eligible_cohort_for_every_variant():
    actual = {'active': 2.0, 'zero_history': 0.0}
    variants = {'history': {'active': 1.0, 'zero_history': 0.0},
                'schedule': {'active': 3.0, 'zero_history': 4.0}}
    common = backtest._comparisons(actual, variants)
    assert common['positions'] == 1
    assert common['variants']['history']['n'] == common['variants']['schedule']['n'] == 1
    assert common['variants']['schedule']['mae'] == 1
