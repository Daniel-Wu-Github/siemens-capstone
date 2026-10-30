"""Unit tests for the proactive replenishment early warning notification engine."""
from siemens_capstone.schemas import BinMaster
from siemens_capstone.ml_engine import DepletionForecast
from siemens_capstone.proactive_alerts import EarlyWarningNotificationEngine, ReplenishmentAlert


def make_forecast(part_id: str, current_stock: float, min_stock: float, p10: float, p50: float) -> DepletionForecast:
    return DepletionForecast(
        part_id=part_id,
        as_of='2026-10-01',
        current_stock=current_stock,
        min_stock=min_stock,
        max_capacity=100.0,
        headroom_units=current_stock - min_stock,
        p10_days_to_min=p10,
        p50_days_to_min=p50,
        p90_days_to_min=p50 + 1.0,
        breach_prob_1d=0.1,
        breach_prob_3d=0.7,
        breach_prob_7d=0.9,
        breach_prob_14d=0.95,
        estimated_crossing_date='2026-10-04',
        trajectory=[],
        explanation='Test forecast',
        method='quantile_gbdt',
    )


def test_alert_critical_when_stock_at_or_below_min():
    engine = EarlyWarningNotificationEngine()
    bm = BinMaster('PART-1', 'Test part', '401', 'BIN-1', min_stock=20.0, max_capacity=100.0)
    fc = make_forecast('PART-1', current_stock=18.0, min_stock=20.0, p10=0.0, p50=0.0)

    alert = engine.evaluate_forecast(fc, bm, {'lead_time_p95_days': 2.0}, warning_buffer_days=3.0)
    assert alert.urgency == 'CRITICAL'
    assert 'Immediate Stockout Risk' in alert.priority_level
    assert alert.recommended_order_qty == 82.0  # 100 - 18


def test_alert_critical_when_depletion_within_lead_time():
    engine = EarlyWarningNotificationEngine()
    bm = BinMaster('PART-2', 'Insulator', '401', 'BIN-2', min_stock=10.0, max_capacity=60.0)
    # Lead time is 2.0 days; P10 is 1.5 days (stockout before normal replenishment can arrive!)
    fc = make_forecast('PART-2', current_stock=25.0, min_stock=10.0, p10=1.5, p50=2.2)

    alert = engine.evaluate_forecast(fc, bm, {'lead_time_p95_days': 2.0}, warning_buffer_days=3.0)
    assert alert.urgency == 'CRITICAL'
    assert 'Expedited' in alert.priority_level


def test_alert_watch_when_depletion_within_buffer_window():
    engine = EarlyWarningNotificationEngine()
    bm = BinMaster('PART-3', 'Bracket', '402', 'BIN-3', min_stock=10.0, max_capacity=60.0)
    # Lead time is 2.0 days, buffer is 3.0 days -> window is <= 5.0 days. P10 is 3.5 days.
    fc = make_forecast('PART-3', current_stock=35.0, min_stock=10.0, p10=3.5, p50=4.5)

    alert = engine.evaluate_forecast(fc, bm, {'lead_time_p95_days': 2.0}, warning_buffer_days=3.0)
    assert alert.urgency == 'WATCH'
    assert 'Proactive Replenishment' in alert.priority_level


def test_alert_healthy_when_depletion_beyond_window():
    engine = EarlyWarningNotificationEngine()
    bm = BinMaster('PART-4', 'Busbar', '403', 'BIN-4', min_stock=10.0, max_capacity=60.0)
    # P10 is 8.0 days, far beyond 2 + 3 = 5 days window
    fc = make_forecast('PART-4', current_stock=55.0, min_stock=10.0, p10=8.0, p50=10.0)

    alert = engine.evaluate_forecast(fc, bm, {'lead_time_p95_days': 2.0}, warning_buffer_days=3.0)
    assert alert.urgency == 'HEALTHY'
    assert 'Healthy Stock' in alert.priority_level


def test_generate_and_prioritize_alerts():
    engine = EarlyWarningNotificationEngine(default_warning_buffer_days=3.0, default_lead_time_days=2.0)
    bms = {
        'P-HEALTHY': BinMaster('P-HEALTHY', 'Part H', '401', 'B-1', 10.0, 50.0),
        'P-CRIT': BinMaster('P-CRIT', 'Part C', '401', 'B-2', 10.0, 50.0),
        'P-WATCH': BinMaster('P-WATCH', 'Part W', '401', 'B-3', 10.0, 50.0),
    }
    forecasts = [
        make_forecast('P-HEALTHY', 45.0, 10.0, p10=9.0, p50=12.0),
        make_forecast('P-WATCH', 25.0, 10.0, p10=3.5, p50=4.2),
        make_forecast('P-CRIT', 15.0, 10.0, p10=1.2, p50=1.8),
    ]

    alerts = engine.generate_alerts(forecasts, bms)
    assert len(alerts) == 3
    # Check prioritization ordering: CRITICAL first, then WATCH, then HEALTHY
    assert alerts[0].part_id == 'P-CRIT'
    assert alerts[0].urgency == 'CRITICAL'
    assert alerts[1].part_id == 'P-WATCH'
    assert alerts[1].urgency == 'WATCH'
    assert alerts[2].part_id == 'P-HEALTHY'
    assert alerts[2].urgency == 'HEALTHY'
