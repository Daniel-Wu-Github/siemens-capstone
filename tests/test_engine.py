from copy import deepcopy

import pytest

from siemens_capstone.engine import predict, evaluate, synthetic_episodes


def material(**changes):
    return dict(id='M1', name='Synthetic part', bin='A-01', area='Assembly',
                unit='PC', stock=60, threshold=20, lead_hours=3, buffer_hours=2,
                snapshot_at='2026-09-29T12:00:00+00:00', scheduled_load=2,
                demand_factor=1, **changes)


def history():
    return [dict(at=f'2026-09-29T{hour:02}:00:00+00:00', used=5, load=1)
            for hour in range(6, 13)]


def test_baseline_and_schedule_adjusted_threshold_time():
    m = material()
    baseline = predict(m, history(), m['snapshot_at'], 'baseline')
    candidate = predict(m, history(), m['snapshot_at'], 'schedule')
    assert baseline['hours_to_threshold'] == 8
    assert candidate['hours_to_threshold'] == 4
    assert candidate['risk'] == 'Watch'
    assert baseline['risk'] == 'Healthy'


def test_future_observations_cannot_change_prediction():
    m = material()
    past = history()
    altered = past + [dict(at='2026-09-29T13:00:00+00:00', used=1000, load=1)]
    assert predict(m, past, m['snapshot_at']) == predict(m, altered, m['snapshot_at'])


def test_at_threshold_is_urgent_even_without_consumption():
    m = material(); m['stock'] = 20
    result = predict(m, [dict(at=m['snapshot_at'], used=0, load=1)] * 6, m['snapshot_at'])
    assert result['hours_to_threshold'] == 0
    assert result['risk'] == 'Critical'


def test_stale_missing_and_zero_demand_are_distinct():
    m = material()
    assert predict(m, history(), '2026-09-29T16:00:00+00:00')['risk'] == 'Stale'
    assert predict(m, [], m['snapshot_at'])['risk'] == 'Unknown'
    zeros = [dict(at=m['snapshot_at'], used=0, load=1)] * 6
    result = predict(m, zeros, m['snapshot_at'])
    assert result['hours_to_threshold'] is None
    assert result['risk'] == 'Healthy'


@pytest.mark.parametrize('field,value', [('stock', -1), ('threshold', float('nan')),
    ('lead_hours', 0), ('scheduled_load', -1), ('unit', 'KG')])
def test_invalid_forecasting_contract_rejected(field, value):
    m = material(); m[field] = value
    with pytest.raises(ValueError):
        predict(m, history(), m['snapshot_at'])


def test_future_stock_snapshot_is_not_used():
    m = material()
    assert predict(m, history(), '2026-09-29T11:00:00+00:00')['risk'] == 'Unknown'


def test_synthetic_evaluation_is_reproducible_and_reports_denominators():
    episodes = synthetic_episodes()
    first = evaluate(episodes)
    assert first == evaluate(deepcopy(episodes))
    assert first['sample_count'] >= 30
    assert first['data_kind'] == 'synthetic'
    for model in first['models']:
        assert model['predicted_events'] == model['true_positives'] + model['false_positives']
        assert model['actual_events'] == model['true_positives'] + model['false_negatives']
        assert 0 <= model['recall'] <= 1
        assert 0 <= model['actionable_recall'] <= model['recall']


def test_evaluation_future_truth_changes_scores_not_predictions():
    episodes = synthetic_episodes()
    changed = deepcopy(episodes)
    for episode in changed:
        episode['actual_hours'] = None
    a, b = evaluate(episodes), evaluate(changed)
    assert [m['predicted_events'] for m in a['models']] == [m['predicted_events'] for m in b['models']]
    assert all(m['actual_events'] == 0 and m['recall'] is None for m in b['models'])
