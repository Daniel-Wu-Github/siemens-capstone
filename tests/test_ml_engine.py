"""Unit tests for the Machine Learning Quantile Depletion Predictor."""
from datetime import date
import pytest

from siemens_capstone.schemas import generate_synthetic_manufacturing_fixtures
from siemens_capstone.features import aggregate_scheduled_bom_demand
from siemens_capstone.ml_engine import (
    QuantileDepletionEstimator,
    BaselineTrailingEstimator,
    evaluate_depletion_models,
)


def test_quantile_depletion_prediction_monotonicity():
    fixtures = generate_synthetic_manufacturing_fixtures()
    # Schedule PART-INSUL-01 demand over several days
    schedule_rows = [
        {'Line Item': '21010', 'Basic Start Date': '2026-10-01'},  # uses 4
        {'Line Item': '21020', 'Basic Start Date': '2026-10-02'},  # uses 10
        {'Line Item': '22010', 'Basic Start Date': '2026-10-04'},  # uses 4
    ]
    demand = aggregate_scheduled_bom_demand(schedule_rows, fixtures['bom'], '2026-10-01')

    # Stock = 20, Min = 10 -> headroom = 10.
    # On Oct 1: 4 used -> 16 left.
    # On Oct 2: 10 used -> 6 left (breaches min 10 on day 1).
    estimator = QuantileDepletionEstimator(scrap_contingency=0.15)
    forecast = estimator.predict(
        part_id='PART-INSUL-01',
        current_stock=20.0,
        bin_master=fixtures['bin_masters']['PART-INSUL-01'],
        demand_schedule=demand['PART-INSUL-01'],
        as_of='2026-10-01',
    )

    assert forecast.current_stock == 20.0
    assert forecast.headroom_units == 10.0
    assert forecast.p10_days_to_min is not None
    assert forecast.p50_days_to_min is not None
    assert forecast.p90_days_to_min is not None
    # P10 is worst-case (earlier), so P10 <= P50 <= P90
    assert forecast.p10_days_to_min <= forecast.p50_days_to_min <= forecast.p90_days_to_min
    assert forecast.breach_prob_3d > 0.5  # Heavy planned demand in 3 days
    assert len(forecast.trajectory) == 15


def test_immediate_breach_when_at_or_below_min():
    fixtures = generate_synthetic_manufacturing_fixtures()
    schedule_rows = []
    demand = aggregate_scheduled_bom_demand(schedule_rows, fixtures['bom'], '2026-10-01')

    estimator = QuantileDepletionEstimator()
    # Min is 10, current stock is 8
    forecast = estimator.predict(
        part_id='PART-INSUL-01',
        current_stock=8.0,
        bin_master=fixtures['bin_masters']['PART-INSUL-01'],
        demand_schedule=demand['PART-INSUL-01'],
        as_of='2026-10-01',
    )

    assert forecast.p10_days_to_min == 0.0
    assert forecast.p50_days_to_min == 0.0
    assert forecast.breach_prob_1d == 1.0
    assert "at or below minimum threshold" in forecast.explanation


def test_zero_scheduled_demand_no_breach():
    fixtures = generate_synthetic_manufacturing_fixtures()
    demand = aggregate_scheduled_bom_demand([], fixtures['bom'], '2026-10-01')

    estimator = QuantileDepletionEstimator()
    forecast = estimator.predict(
        part_id='PART-INSUL-01',
        current_stock=50.0,
        bin_master=fixtures['bin_masters']['PART-INSUL-01'],
        demand_schedule=demand['PART-INSUL-01'],
        as_of='2026-10-01',
    )

    assert forecast.p50_days_to_min is None
    assert forecast.breach_prob_7d == 0.0
    assert "safely above minimum" in forecast.explanation


def test_baseline_trailing_estimator():
    estimator = BaselineTrailingEstimator()
    # Stock 30, min 10 -> headroom 20. Trailing rate 5/day -> 4 days
    res = estimator.predict('PART-A', current_stock=30.0, min_stock=10.0, trailing_daily_rate=5.0, as_of='2026-10-01')
    assert res['days_to_min'] == 4.0

    # Zero rate
    res_zero = estimator.predict('PART-A', current_stock=30.0, min_stock=10.0, trailing_daily_rate=0.0, as_of='2026-10-01')
    assert res_zero['days_to_min'] is None


def test_evaluate_depletion_models():
    episodes = [
        {'actual_days_to_min': 2.5, 'lead_time_days': 2.0, 'baseline_days': 4.0, 'schedule_mrp_days': 2.8, 'quantile_p10_days': 2.2, 'quantile_p50_days': 2.6},
        {'actual_days_to_min': 5.0, 'lead_time_days': 2.0, 'baseline_days': 9.0, 'schedule_mrp_days': 5.2, 'quantile_p10_days': 4.6, 'quantile_p50_days': 5.1},
        {'actual_days_to_min': None, 'lead_time_days': 2.0, 'baseline_days': None, 'schedule_mrp_days': None, 'quantile_p10_days': None, 'quantile_p50_days': None},
    ]
    report = evaluate_depletion_models(episodes, horizon_days=7.0)
    assert report['episode_count'] == 3
    assert len(report['models']) == 4

    # Quantile P10 has high recall for early events
    p10_model = next(m for m in report['models'] if m['model'] == 'quantile_p10')
    assert p10_model['true_positives'] == 2
    assert p10_model['recall'] == 1.0
    assert p10_model['actionable_recall'] == 1.0
    assert p10_model['mae_days'] is not None
