from copy import deepcopy

import pytest

from siemens_capstone.analytics import analyze
from siemens_capstone.store import Store


def records():
    first = {'Transfer Order Number': 'T1', 'Transfer order item': '1', 'Material': 'PART-A',
             'Dest.Storage Bin': 'A1', 'Dest. Storage Type': 'SM', 'Dest.target quantity': 90,
             '_created_at_local': '2026-01-01T08:00:00', '_confirmed_at_local': '2026-01-01T10:00:00'}
    second = dict(first, **{'Transfer Order Number': 'T2', 'Dest.target quantity': 60,
                           '_created_at_local': '2026-01-30T08:00:00', '_confirmed_at_local': '2026-01-30T08:00:00'})
    invalid = dict(first, **{'Transfer Order Number': 'T3', '_created_at_local': '2026-01-30T08:00:00',
                            '_confirmed_at_local': '2026-01-29T08:00:00'})
    return {
        'BIN_STATUS_EXAMPLE': [
            {'Material': 'PART-A', 'Material Description': 'Synthetic bracket', 'Storage Type': 'SM', 'Storage Bin': 'A1', 'Available stock': 10, 'Base Unit of Measure': 'PC'},
            {'Material': 'PART-B', 'Material Description': 'Synthetic bolt', 'Storage Type': 'SM', 'Storage Bin': 'B1', 'Available stock': 0, 'Base Unit of Measure': 'EA'},
        ],
        'HISTORICAL_SM_REPLENISHMENTS': [first, second, deepcopy(first), invalid],
        'HISTORICAL_PROD_DATA': [
            {'TransactionTime': '2026-01-01T10:00:00', 'productionorderOrdNr': 'P1', 'statusstringDescription': 'Passed', 'JobTraceTime (MIN)': 10, 'stationDescr': 'Build'},
            {'TransactionTime': '2026-01-01T11:00:00', 'productionorderOrdNr': 'P1', 'statusstringDescription': 'On hold', 'JobTraceTime (MIN)': 20, 'stationDescr': 'Build'},
        ],
        'PLANNED_PROD_SCHEDULE': [
            {'Sales Order': 'SO1', 'Line Item': '1', '_start_at_local': '2026-02-01T00:00:00'},
            {'Sales Order': 'SO1', 'Line Item': '1', '_start_at_local': '2026-02-01T00:00:00'},
            {'Sales Order': 'SO2', 'Line Item': '1', '_start_at_local': '2026-02-03T00:00:00'},
        ],
    }


def test_real_data_summary_uses_explicit_grains_and_exclusions():
    result = analyze(records(), '1900')
    assert result['inventory_summary']['positions'] == 2
    assert result['inventory_summary']['empty_positions'] == 1
    assert result['production']['events'] == 2
    assert result['production']['distinct_orders'] == 1
    assert result['production']['passed_events'] == 1
    assert result['schedule']['source_rows'] == 3
    assert result['schedule']['unique_lines_by_date'] == 2
    assert result['transfers']['valid_events'] == 2
    assert result['transfers']['duplicate_events'] == 1
    assert result['transfers']['invalid_time_order'] == 1
    assert result['transfers']['median_confirmation_hours'] == 1
    assert result['transfers']['zero_duration_events'] == 1


def test_proxy_uses_transfer_targets_and_never_claims_consumption():
    result = analyze(records(), '1900')
    row = next(r for r in result['inventory'] if r['material'] == 'PART-A')
    assert row['window_transfer_quantity'] == 150
    assert row['window_days'] == 30
    assert row['target_quantity_per_day'] == 5
    assert row['stock_to_target_days'] == 2
    assert result['forecast_validated'] is False
    assert result['snapshot_as_of'] is None


def test_missing_and_ambiguous_inputs_do_not_become_zero_demand():
    data = records()
    data['BIN_STATUS_EXAMPLE'][0]['Available stock'] = None
    result = analyze(data, '1900')
    row = next(r for r in result['inventory'] if r['material'] == 'PART-A')
    assert row['stock'] is None
    assert row['stock_to_target_days'] is None


def test_schedule_and_production_do_not_get_joined_into_material_use():
    data = records()
    original = analyze(data, '1900')
    data['PLANNED_PROD_SCHEDULE'] *= 100
    changed = analyze(data, '1900')
    assert original['inventory'] == changed['inventory']
    assert changed['schedule']['source_rows'] == 300


def test_duplicate_stock_is_not_added_twice_and_missing_identifiers_reported():
    data = records()
    data['BIN_STATUS_EXAMPLE'].append(deepcopy(data['BIN_STATUS_EXAMPLE'][0]))
    data['BIN_STATUS_EXAMPLE'].append({'Material': 'MISSING-BIN', 'Available stock': 10})
    result = analyze(data, '1900')
    row = next(r for r in result['inventory'] if r['material'] == 'PART-A')
    assert row['stock'] == 10
    assert result['inventory_summary']['duplicate_rows'] == 1
    assert result['inventory_summary']['missing_key_rows'] == 1


def test_conflicting_duplicate_transfers_and_stock_are_excluded_from_scenarios():
    data = records()
    conflicting = deepcopy(data['HISTORICAL_SM_REPLENISHMENTS'][0])
    conflicting['Dest.target quantity'] = 999
    data['HISTORICAL_SM_REPLENISHMENTS'].append(conflicting)
    result = analyze(data, '1900')
    assert result['transfers']['conflicting_duplicate_keys'] == 1
    assert result['transfers']['valid_events'] == 1
    data['BIN_STATUS_EXAMPLE'].append(dict(data['BIN_STATUS_EXAMPLE'][0], **{'Available stock': 999}))
    result = analyze(data, '1900')
    row = next(r for r in result['inventory'] if r['material'] == 'PART-A')
    assert row['stock'] is None and row['scenario_eligible'] is False


def test_small_positive_target_rate_remains_scenario_eligible():
    data = records()
    for row in data['HISTORICAL_SM_REPLENISHMENTS']:
        row['Dest.target quantity'] = 0.00001
    result = analyze(data, '1900')
    row = next(r for r in result['inventory'] if r['material'] == 'PART-A')
    assert row['scenario_eligible']
    assert row['target_quantity_per_day'] > 0


def reference_records():
    """Synthetic scenario: US2-prefixed BOM part, a shared part, tolerance rows, completed orders, a non-replenishment transfer."""
    def bin_row(material, bin_, stock, typ='401'):
        return {'Material': material, 'Material Description': 'Synthetic', 'Storage Type': typ, 'Storage Bin': bin_, 'Available stock': stock, 'Base Unit of Measure': 'PC'}
    def transfer(number, material, bin_, qty, src, dest='401', day='2026-01-30'):
        return {'Transfer Order Number': number, 'Transfer order item': '1', 'Material': material, 'Dest.Storage Bin': bin_,
                'Source Storage Type': src, 'Dest. Storage Type': dest, 'Dest.target quantity': qty,
                '_created_at_local': day+'T08:00:00', '_confirmed_at_local': day+'T09:00:00'}
    def prod(order, status, item, produced):
        return {'productionorderOrdNr': order, 'productionorderStatus': status, 'productionorderSalesOrderLineItem': item,
                'productionorderProducedTime': produced, 'TransactionTime': '2026-01-01T00:00:00', 'statusstringDescription': 'Passed'}
    return {
        'BIN_STATUS_EXAMPLE': [bin_row('PART-A', 'A1', 10), bin_row('US2:PART-C', 'C1', 40), bin_row('PART-C', 'C2', 5), bin_row('PART-D', 'D1', 3),
                               bin_row('PART-E', 'E1', 50, '409'), bin_row('PART-F', 'F1', 1, '403')],
        'HISTORICAL_SM_REPLENISHMENTS': [transfer('T1', 'PART-A', 'A1', 30, '001'), transfer('T2', 'PART-A', 'A1', 30, '011', day='2026-01-01'),
                                         transfer('T3', 'PART-A', 'A1', 500, '401', '001'), transfer('T4', 'PART-A', 'A1', 700, '401', '402'),
                                         transfer('T5', 'PART-A', 'A1', 900, '900', '900')],
        'HISTORICAL_PROD_DATA': [prod('P1', 20, '10', '2026-01-20T08:00:00'), prod('P1', 70, '10', '2026-01-21T08:00:00'),
                                 prod('P2', 70, '10', '2026-01-30T08:00:00'), prod('P3', 11, '10', '2026-01-30T08:00:00'),
                                 prod('P4', 70, '99', '2026-01-30T08:00:00')],
        'PLANNED_PROD_SCHEDULE': [{'Sales Order': 'S1', 'Line Item': '0010', '_start_at_local': '2026-02-02T00:00:00'},
                                  {'Sales Order': 'S2', 'Line Item': '10', '_start_at_local': '2026-02-20T00:00:00'},
                                  {'Sales Order': 'S3', 'Line Item': '10', '_start_at_local': '2026-03-30T00:00:00'},
                                  {'Sales Order': 'S4', 'Line Item': '77', '_start_at_local': '2026-02-03T00:00:00'}],
    }


def reference_files():
    tol = [{'Part': 'PART-A', 'Location': 'A1', 'Type': '401', 'Min': 12, 'Max': 20}, {'Part': 'PART-D', 'Location': 'D1', 'Min': 0, 'Max': 0},
           {'Part': 'PART-F', 'Location': 'F1', 'Min': 5, 'Max': 5}, {'Part': 'US2:PART-C', 'Location': 'C1', 'Min': 4, 'Max': 60}]
    bom = [{'Item': 10, 'Part': 'PART-A', 'Qty': 2}, {'Item': 10, 'Part': 'US2:PART-C', 'Qty': 1}, {'Item': 10, 'Part': 'PART-E', 'Qty': 1.5}]
    return dict(tolerances=tol, bom=bom, snapshots=[])


def position(result, material):
    return next(p for p in result['inventory'] if p['material'] == material)


def test_transfers_are_classified_and_only_replenishments_feed_rates():
    result = analyze(reference_records(), '1900')
    assert result['transfers']['categories'] == dict(replenishment=2, return_to_warehouse=1, supermarket_transfer=1, other=1)
    a = position(result, 'PART-A')
    assert a['window_transfer_quantity'] == 60 and a['replen_qty_per_day'] == a['target_quantity_per_day'] == 2
    assert result['transfers']['valid_events'] == 5
    assert result['transfers']['weekly'] and sum(w['count'] for w in result['transfers']['weekly']) == 2


def test_missing_source_column_means_replenishment_and_no_references_is_safe():
    result = analyze(records(), '1900', None)
    assert result['transfers']['categories']['replenishment'] == 2
    p = position(result, 'PART-A')
    assert p['tolerance_flag'] == 'missing' and p['min'] is None and p['at_or_below_min'] is False and p['fill_pct'] is None
    assert p['bom_status'] == 'not_in_bom' and p['bom_qty_per_day'] is None
    assert p['snapshot_history'] == [] and p['observed_depletion_per_day'] is None
    assert result['references']['tolerances']['matched_positions'] == 0 and result['horizon_default_hours'] == 8 and result['version'] == '2.1-refills1'


def test_supermarket_names_and_unmapped_types():
    result = analyze(reference_records(), '1900')
    assert position(result, 'PART-A')['supermarket'] == 'Silver'
    assert position(result, 'PART-F')['supermarket'] == 'Blue'
    assert position(result, 'PART-E')['supermarket'] == 'Type 409'


def test_tolerance_flags_fill_and_days_to_minimum():
    result = analyze(reference_records(), '1900', reference_files())
    a = position(result, 'PART-A')
    assert (a['min'], a['max'], a['tolerance_flag'], a['fill_pct'], a['at_or_below_min']) == (12, 20, 'ok', 50, True)
    assert a['days_to_min_replen'] == 0
    assert position(result, 'PART-D')['tolerance_flag'] == 'zero_min' and position(result, 'PART-D')['at_or_below_min'] is False
    assert position(result, 'PART-F')['tolerance_flag'] == 'ok'  # min == max is valid, as on the Stock alerts page
    assert position(result, 'PART-E')['tolerance_flag'] == 'missing'
    assert result['references']['tolerances'] == dict(loaded=True, rows=4, matched_positions=4, zero_min=1, invalid=0, max_not_set=0)
    assert result['inventory'][0]['material'] == 'PART-A'  # below-min positions sort first
    data = reference_records()
    data['BIN_STATUS_EXAMPLE'][0]['Available stock'] = 22
    assert position(analyze(data, '1900', reference_files()), 'PART-A')['days_to_min_replen'] == 5


def test_bom_demand_shared_parts_and_planned_load():
    result = analyze(reference_records(), '1900', reference_files())
    window = result['production']['bom_window_days']
    assert window == 10 and result['production']['order_status_counts']['70']['rows'] == 3
    a, e = position(result, 'PART-A'), position(result, 'PART-E')
    assert a['bom_status'] == 'ok' and a['bom_qty_per_day'] == 2 * 2 / 10  # P1 and P2 complete on item 10; P4 is item 99
    assert e['bom_qty_per_day'] == 2 * 1.5 / 10
    assert position(result, 'US2:PART-C')['bom_status'] == position(result, 'PART-C')['bom_status'] == 'shared'
    assert position(result, 'PART-C')['bom_qty_per_day'] is None
    assert position(result, 'PART-D')['bom_status'] == 'not_in_bom'
    assert a['planned_qty_next_30d'] == 2 * 2 and e['planned_qty_next_30d'] == 1.5 * 2  # S1 and S2 inside 30 days; S3 is outside
    assert a['days_to_min_bom'] == 0
    assert result['references']['bom'] == dict(loaded=True, rows=3, line_items=1, parts=3, production_orders_covered=3,
                                               production_orders_total=4, schedule_rows_covered=3, schedule_rows_total=4)
    assert sum(w['bom_lines'] for w in result['schedule']['weekly']) == 3 and sum(w['lines'] for w in result['schedule']['weekly']) == 4
    assert sum(w['orders'] for w in result['production']['completed_orders_weekly']) == 3
    assert sum(d['orders'] for d in result['production']['completed_orders_daily']) == 3


def test_snapshot_history_and_observed_depletion_ignore_receipts():
    refs = reference_files()
    def snap(day, *stocks): return dict(as_of=day, rows=[{'Material': 'PART-A', 'Storage Bin': 'A1', 'Storage Type': '401', 'Available stock': s} for s in stocks])
    refs['snapshots'] = [snap('2026-10-05', 20), snap('2026-10-01', 30), snap('2026-10-03', 24)]
    a = position(analyze(reference_records(), '1900', refs), 'PART-A')
    assert a['snapshot_history'] == [dict(as_of='2026-10-01', stock=30), dict(as_of='2026-10-03', stock=24), dict(as_of='2026-10-05', stock=20)]
    assert a['observed_depletion_per_day'] == 2.5  # (6/2 + 4/2) / 2
    refs['snapshots'] = [snap('2026-10-01', 5), snap('2026-10-02', 25)]
    assert position(analyze(reference_records(), '1900', refs), 'PART-A')['observed_depletion_per_day'] == 0.0
    refs['snapshots'] = [snap('2026-10-01', 5)]
    assert position(analyze(reference_records(), '1900', refs), 'PART-A')['observed_depletion_per_day'] is None
