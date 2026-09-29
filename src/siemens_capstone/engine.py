"""Point-in-time estimators and deterministic synthetic evaluation.

The schedule estimator fits units per load from prior observations. It has no
access to the future outcome and is intentionally simpler than a trained ML model.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import random
from statistics import mean

AS_OF = '2026-09-29T12:00:00+00:00'
VERSION = '1.0.0'


def timestamp(value):
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Forecast timestamps must include a timezone.')
    return dt


def number(value, name, minimum=0, maximum=1e9):
    if isinstance(value, bool):
        raise ValueError(f'{name} must be a number.')
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{name} must be a number.') from None
    if not math.isfinite(n) or n < minimum or n > maximum:
        raise ValueError(f'{name} must be between {minimum} and {maximum}.')
    return n


def predict(material, history, as_of, method='schedule'):
    if method not in ('baseline', 'schedule'):
        raise ValueError('Unknown estimator.')
    if material.get('unit') not in ('PC', 'EA'):
        raise ValueError('Only count units PC and EA are supported; convert units explicitly first.')
    stock = number(material['stock'], 'Stock')
    threshold = number(material['threshold'], 'Threshold')
    lead = number(material['lead_hours'], 'Lead time', 0.1, 168)
    buffer = number(material['buffer_hours'], 'Buffer', 0, 168)
    load = number(material['scheduled_load'], 'Scheduled load', 0, 10)
    factor = number(material.get('demand_factor', 1), 'Demand factor', 0.25, 3)
    now, snapshot = timestamp(as_of), timestamp(material['snapshot_at'])
    result = dict(method=method, risk='Unknown', rate=None, hours_to_threshold=None,
                  crossing_at=None, reason='', as_of=as_of, observations=0)
    if snapshot > now:
        return dict(result, reason='The stock snapshot is later than the forecast time.')
    if (now - snapshot).total_seconds() > 7200:
        return dict(result, risk='Stale', reason='Stock observation is more than two hours old; refresh before acting.')
    past = sorted([h for h in history if timestamp(h['at']) <= now], key=lambda h: timestamp(h['at']))
    if not past:
        return dict(result, reason='No consumption observations are available at this time.')
    if (now - timestamp(past[-1]['at'])).total_seconds() > 7200:
        return dict(result, risk='Stale', reason='Consumption observations are more than two hours old.')
    window = past[-6:] if method == 'baseline' else past[-12:]
    for h in window:
        number(h['used'], 'Hourly consumption')
        number(h['load'], 'Historical load', 0.01, 10)
    rate = mean(float(h['used']) for h in window) if method == 'baseline' else mean(float(h['used']) / float(h['load']) for h in window) * load * factor
    hours = 0 if stock <= threshold else ((stock - threshold) / rate if rate > 0 else None)
    risk = 'Healthy' if hours is None or hours > lead + buffer else ('Critical' if hours <= lead else 'Watch')
    reason = ('Stock is at or below minimum tolerance.' if stock <= threshold else
              'No observed consumption; no finite crossing is estimated.' if hours is None else
              f'Estimated minimum crossing in {hours:.1f}h; warehouse lead time {lead:g}h + buffer {buffer:g}h.')
    return dict(result, risk=risk, rate=round(rate, 4), hours_to_threshold=round(hours, 4) if hours is not None else None,
                crossing_at=(now + timedelta(hours=hours)).isoformat() if hours is not None and hours < 87600 else None,
                reason=reason, observations=len(window))


def demo_materials():
    rng = random.Random(482)
    names = ['Copper terminal lug', 'Panel mounting bracket', 'Control wire spool', 'Hex socket bolt',
             'Insulating spacer', 'Door hinge assembly', 'Grounding strap', 'DIN rail segment',
             'Cable entry gland', 'Contact block', 'Busbar support', 'Enclosure seal']
    areas = ['Mechanical', 'Electrical', 'Finishing']
    materials = []
    for i, name in enumerate(names):
        base = 3 + (i % 5) * 1.4
        load = [1.7, 1.3, 1.6, 1, 0.8, 1.2][i % 6]
        threshold = [20, 15, 10, 30][i % 4]
        hours = [1.4, 3.8, 2.2, 12, 18, 6, 22, 16, 3.1, 28, 9, 32][i]
        stock = round(threshold + hours * base * load)
        hist = []
        for h in range(24):
            hist.append(dict(at=(timestamp(AS_OF) - timedelta(hours=23-h)).isoformat(),
                             used=round(base * rng.uniform(0.82, 1.18), 3), load=1))
        cumulative = stock
        for row in reversed(hist):
            row['stock'] = round(cumulative, 2)
            cumulative += row['used']
        materials.append(dict(id=f'DEM-{101+i}', name=name, area=areas[i % 3], bin=f'{chr(65+i%3)}-{i+1:02}',
                              unit='PC', stock=stock, threshold=threshold, lead_hours=3, buffer_hours=2,
                              scheduled_load=load, demand_factor=1, snapshot_at=AS_OF, history=hist))
    return materials


def synthetic_episodes():
    rng = random.Random(48209)
    episodes = []
    for i in range(60):
        base = rng.uniform(2, 12)
        load = rng.choice([0.5, 0.75, 1.2, 1.5, 2.0])
        gap = rng.uniform(2, 20) * base
        hist = [dict(at=(timestamp(AS_OF)-timedelta(hours=11-h)).isoformat(),
                     used=base * rng.uniform(0.7, 1.3), load=1) for h in range(12)]
        # Future truth is generated separately and is never passed to predict().
        future_rate = base * load * rng.uniform(0.75, 1.25)
        actual = gap / future_rate
        m = dict(id=f'EP-{i+1:03}', unit='PC', stock=20+gap, threshold=20,
                 lead_hours=3, buffer_hours=9, snapshot_at=AS_OF, scheduled_load=load,
                 demand_factor=1)
        episodes.append(dict(material=m, history=hist, as_of=AS_OF, horizon_hours=12,
                             actual_hours=actual if actual <= 12 else None))
    return episodes


def evaluate(episodes):
    models, examples = [], []
    for method, label in [('baseline', 'Trailing average'), ('schedule', 'Schedule adjusted')]:
        tp = fp = fn = actionable = predicted = actual = available = 0
        errors, lead_times = [], []
        for ep in episodes:
            p = predict(ep['material'], ep['history'], ep['as_of'], method)
            h, truth, horizon = p['hours_to_threshold'], ep['actual_hours'], ep['horizon_hours']
            known = p['risk'] not in ('Unknown', 'Stale')
            alarm = known and h is not None and h <= horizon
            event = truth is not None and truth <= horizon
            available += known; predicted += alarm; actual += event
            tp += alarm and event; fp += alarm and not event; fn += event and not alarm
            if alarm and event:
                lead_times.append(truth)
                actionable += truth >= ep['material']['lead_hours']
            if event and known and h is not None:
                errors.append(abs(h-truth))
            if method == 'schedule':
                examples.append(dict(id=ep['material']['id'], predicted_hours=h,
                                     actual_hours=round(truth, 2) if truth is not None else None,
                                     outcome='Hit' if alarm and event else 'False alert' if alarm else 'Miss' if event else 'No event'))
        models.append(dict(id=method, name=label, true_positives=tp, false_positives=fp, false_negatives=fn,
                           predicted_events=predicted, actual_events=actual, actionable_events=actionable,
                           precision=tp/predicted if predicted else None, recall=tp/actual if actual else None,
                           actionable_recall=actionable/actual if actual else None,
                           mae_hours=mean(errors) if errors else None, error_samples=len(errors),
                           mean_lead_hours=mean(lead_times) if lead_times else None,
                           coverage=available/len(episodes) if episodes else 0))
    return dict(data_kind='synthetic', engine_version=VERSION, sample_count=len(episodes), models=models,
                examples=examples, checksum=hashlib.sha256(json.dumps(episodes, sort_keys=True).encode()).hexdigest(),
                protocol='60 independent synthetic episodes; 12 prior hourly observations; 12h held-out outcome; 3h replenishment lead time. No hyperparameter selection on outcomes.',
                limitations='Illustrative simulator, not Siemens validation. The generator includes a schedule effect that favors the schedule estimator. No historical stockout labels are available from the supplied workbook.')
