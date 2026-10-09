"""Unit tests for the feature engineering and planned demand aggregation pipeline."""
from datetime import date
from siemens_capstone.schemas import generate_synthetic_manufacturing_fixtures
from siemens_capstone.features import (
    aggregate_scheduled_bom_demand,
    compute_inventory_headroom,
    compute_part_criticality,
    extract_replenishment_latency,
    build_feature_vector,
)


def test_aggregate_scheduled_bom_demand():
    fixtures = generate_synthetic_manufacturing_fixtures()
    schedule_rows = [
        {'Line Item': '21010', 'Basic Start Date': '2026-10-01'},
        {'Line Item': '21020', 'Basic Start Date': '2026-10-02'},
        {'Line Item': '22010', 'Basic Start Date': '2026-10-05'},
    ]
    # As of 2026-10-01
    demand = aggregate_scheduled_bom_demand(schedule_rows, fixtures['bom'], '2026-10-01')

    # PART-INSUL-01:
    # 21010 uses 4 on Oct 1
    # 21020 uses 10 on Oct 2
    # 22010 uses 4 on Oct 5
    p_insul = demand['PART-INSUL-01']
    assert p_insul.demand_1d == 4.0   # Oct 1
    assert p_insul.demand_3d == 14.0  # Oct 1 + Oct 2 = 4 + 10
    assert p_insul.demand_7d == 18.0  # Oct 1 + Oct 2 + Oct 5 = 4 + 10 + 4
    assert p_insul.contributing_orders == 3


def test_compute_inventory_headroom():
    headroom = compute_inventory_headroom(current_stock=45.0, min_stock=10.0, max_capacity=50.0)
    assert headroom['headroom'] == 35.0
    assert headroom['fill_ratio'] == 0.9
    assert headroom['is_below_min'] == 0.0

    # Stock at or below minimum
    breach = compute_inventory_headroom(current_stock=8.0, min_stock=10.0, max_capacity=50.0)
    assert breach['headroom'] == -2.0
    assert breach['is_below_min'] == 1.0


def test_compute_part_criticality():
    fixtures = generate_synthetic_manufacturing_fixtures()
    criticality = compute_part_criticality(fixtures['bom'])

    # PART-INSUL-01 is used in 21010, 21020, 22010
    assert criticality['PART-INSUL-01']['distinct_line_items'] == 3
    assert criticality['PART-INSUL-01']['is_shared_across_multiple_lines'] == 1.0

    # PART-BRACKET-01 is only in 21010
    assert criticality['PART-BRACKET-01']['distinct_line_items'] == 1
    assert criticality['PART-BRACKET-01']['is_shared_across_multiple_lines'] == 0.0


def test_extract_replenishment_latency():
    repl_rows = [
        {'Material': 'PART-A', '_created_at_local': '2026-09-01T08:00:00', '_confirmed_at_local': '2026-09-02T08:00:00'},  # 24h
        {'Material': 'PART-A', '_created_at_local': '2026-09-03T08:00:00', '_confirmed_at_local': '2026-09-04T20:00:00'},  # 36h
        {'Material': 'PART-A', '_created_at_local': '2026-09-05T08:00:00', '_confirmed_at_local': '2026-09-07T08:00:00'},  # 48h
    ]
    stats = extract_replenishment_latency(repl_rows)
    assert 'PART-A' in stats
    assert stats['PART-A']['lead_time_p50_hours'] == 36.0
    assert stats['PART-A']['lead_time_p95_days'] > 1.5


def test_build_feature_vector():
    fixtures = generate_synthetic_manufacturing_fixtures()
    schedule_rows = [{'Line Item': '21010', 'Basic Start Date': '2026-10-01'}]
    demand = aggregate_scheduled_bom_demand(schedule_rows, fixtures['bom'], '2026-10-01')

    vec = build_feature_vector(
        part_id='PART-INSUL-01',
        current_stock=25.0,
        bin_master=fixtures['bin_masters']['PART-INSUL-01'],
        demand_schedule=demand['PART-INSUL-01'],
    )
    assert vec['current_stock'] == 25.0
    assert vec['min_stock'] == 10.0
    assert vec['headroom_units'] == 15.0
    assert vec['demand_1d'] == 4.0
    assert 'coverage_days_3d' in vec
    assert 'lead_time_p95_days' in vec
