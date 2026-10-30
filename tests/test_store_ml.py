"""Unit tests for ML store methods and API payloads."""
import pytest
from siemens_capstone.store import Store


def test_store_ml_forecast(tmp_path):
    store = Store(tmp_path / 'test_ml.sqlite3')
    res = store.ml_forecast('demo', warning_buffer_days=3.0)

    assert res['dataset_id'] == 'demo'
    assert res['warning_buffer_days'] == 3.0
    assert 'forecasts' in res and len(res['forecasts']) >= 5
    assert 'alerts' in res and len(res['alerts']) >= 5

    # Check forecast structure
    fc = res['forecasts'][0]
    assert 'p10_days_to_min' in fc
    assert 'p50_days_to_min' in fc
    assert 'breach_prob_3d' in fc
    assert 'trajectory' in fc

    # Check alert structure
    alert = res['alerts'][0]
    assert 'urgency' in alert
    assert 'priority_level' in alert
    assert 'recommended_order_qty' in alert


def test_store_ml_evaluate(tmp_path):
    store = Store(tmp_path / 'test_ml_eval.sqlite3')
    eval_res = store.ml_evaluate('demo', horizon_days=7.0)

    assert eval_res['horizon_days'] == 7.0
    assert eval_res['episode_count'] >= 3
    assert len(eval_res['models']) == 4

    models = {m['model']: m for m in eval_res['models']}
    assert 'quantile_p10' in models
    assert 'schedule_mrp' in models
    assert 'baseline' in models
