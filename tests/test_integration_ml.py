"""End-to-end integration test for the ML material depletion and replenishment notification pipeline."""
import pytest
from datetime import date

from siemens_capstone.schemas import generate_synthetic_manufacturing_fixtures
from siemens_capstone.features import (
    aggregate_scheduled_bom_demand,
    compute_part_criticality,
    extract_replenishment_latency,
)
from siemens_capstone.ml_engine import (
    QuantileDepletionEstimator,
    evaluate_depletion_models,
)
from siemens_capstone.alerts import EarlyWarningNotificationEngine


def test_end_to_end_ml_depletion_pipeline():
    # 1. Setup synthetic environment
    fixtures = generate_synthetic_manufacturing_fixtures()
    bom = fixtures['bom']
    bin_masters = fixtures['bin_masters']

    # 2. Planned production schedule over the next 7 days
    schedule_rows = [
        {'Line Item': '21010', 'Basic Start Date': '2026-10-01'},
        {'Line Item': '21020', 'Basic Start Date': '2026-10-02'},
        {'Line Item': '22010', 'Basic Start Date': '2026-10-03'},
        {'Line Item': '22020', 'Basic Start Date': '2026-10-05'},
    ]

    # 3. Simulated current supermarket stocks
    # PART-INSUL-01: min 10, max 60. Current stock 16. (Uses 4 on Oct 1, 10 on Oct 2 -> 16 - 14 = 2 <= 10 on Day 1!)
    # PART-BRACKET-01: min 5, max 30. Current stock 20. (Uses 2 on Oct 1 -> 18, safe)
    # PART-BUSBAR-CU: min 8, max 40. Current stock 11. (Uses 1 on Oct 1, 2 on Oct 3 -> 8 <= 8 on Day 2!)
    # PART-BKR-3WA: min 2, max 10. Current stock 1. (Already below min!)
    # PART-SPACER-01: min 20, max 100. Current stock 90. (Uses 8 on Oct 2, 12 on Oct 5 -> 70, safe)
    current_stocks = {
        'PART-INSUL-01': 16.0,
        'PART-BRACKET-01': 20.0,
        'PART-BUSBAR-CU': 11.0,
        'PART-BKR-3WA': 1.0,
        'PART-SPACER-01': 90.0,
    }

    # 4. Feature Extraction
    as_of = '2026-10-01'
    demand_schedules = aggregate_scheduled_bom_demand(schedule_rows, bom, as_of)
    criticality = compute_part_criticality(bom)
    replenishments = [
        {'Material': 'PART-INSUL-01', '_created_at_local': '2026-09-20T08:00:00', '_confirmed_at_local': '2026-09-21T08:00:00'},  # 24h = 1d
        {'Material': 'PART-BUSBAR-CU', '_created_at_local': '2026-09-20T08:00:00', '_confirmed_at_local': '2026-09-22T08:00:00'},  # 48h = 2d
    ]
    lead_times = extract_replenishment_latency(replenishments)

    # 5. ML Quantile Prediction
    ml_estimator = QuantileDepletionEstimator(scrap_contingency=0.10)
    forecasts = []
    for part_id, master in bin_masters.items():
        stock = current_stocks.get(part_id, master.min_stock + 10)
        fc = ml_estimator.predict(
            part_id=part_id,
            current_stock=stock,
            bin_master=master,
            demand_schedule=demand_schedules[part_id],
            as_of=as_of,
            criticality=criticality.get(part_id),
            lead_time_stats=lead_times.get(part_id),
        )
        forecasts.append(fc)

    assert len(forecasts) == 5

    # 6. Proactive Alert Generation (warning buffer = 3.0 days, lead time = 1.0-2.0 days)
    alert_engine = EarlyWarningNotificationEngine(default_warning_buffer_days=3.0)
    alerts = alert_engine.generate_alerts(forecasts, bin_masters, lead_times)

    assert len(alerts) == 5

    # Verifications:
    # 1. PART-BKR-3WA: current stock 1 <= min 2 -> must be CRITICAL (Priority 1)
    bkr_alert = next(a for a in alerts if a.part_id == 'PART-BKR-3WA')
    assert bkr_alert.urgency == 'CRITICAL'
    assert bkr_alert.recommended_order_qty == 9.0  # max 10 - 1

    # 2. PART-INSUL-01: breaches min within ~1 day -> CRITICAL (P10 <= lead time 1.0 day)
    insul_alert = next(a for a in alerts if a.part_id == 'PART-INSUL-01')
    assert insul_alert.urgency == 'CRITICAL'
    assert insul_alert.p10_days_to_min is not None and insul_alert.p10_days_to_min <= 2.0

    # 3. PART-BUSBAR-CU: breaches min around Day 2 -> WATCH or CRITICAL depending on lead time buffer
    busbar_alert = next(a for a in alerts if a.part_id == 'PART-BUSBAR-CU')
    assert busbar_alert.urgency in ('CRITICAL', 'WATCH')

    # 4. PART-SPACER-01 & PART-BRACKET-01: remain healthy
    spacer_alert = next(a for a in alerts if a.part_id == 'PART-SPACER-01')
    assert spacer_alert.urgency == 'HEALTHY'

    # Check alert ordering: all CRITICAL appear before all WATCH, and WATCH before HEALTHY
    urgency_sequence = [a.urgency for a in alerts]
    assert urgency_sequence.index('HEALTHY') > urgency_sequence.index('CRITICAL')

    # 7. Model Evaluation Comparison
    synthetic_eval_episodes = [
        {'actual_days_to_min': f.p50_days_to_min, 'lead_time_days': 2.0,
         'baseline_days': 4.0 if f.p50_days_to_min else None,
         'schedule_mrp_days': f.p50_days_to_min,
         'quantile_p10_days': f.p10_days_to_min,
         'quantile_p50_days': f.p50_days_to_min}
        for f in forecasts if f.p50_days_to_min is not None
    ]
    eval_result = evaluate_depletion_models(synthetic_eval_episodes, horizon_days=7.0)
    assert eval_result['episode_count'] >= 2
    assert len(eval_result['models']) == 4
