"""Phase 2 proactive stock forecast: when does each bin reach its minimum, versus the lead time to restock it?

Pure functions (no DB, no I/O). The demand rate blends up to three per-day inputs: history (transfer
cadence), schedule (planned production x BOM), and observed (snapshot depletion). A schedule that diverges
from history drops history, so the plan outweighs the past.
"""
import copy
import math
import os

from .alerts import label, position_key

DEFAULT_SETTINGS = dict(lead_hours_stocked=3.0, lead_hours_fab=3.0, safety_hours=24.0, watch_hours=72.0,
                        weights=dict(history=1.0, schedule=1.0, observed=0.0), divergence_pct=50.0)
RANGES = dict(lead_hours_stocked=(0, 720), lead_hours_fab=(0, 720), safety_hours=(0, 168), watch_hours=(0, 720), divergence_pct=(0, 1000))
WEIGHT_RANGE = (0, 10)
ENV = dict(LEAD_HOURS_STOCKED='lead_hours_stocked', LEAD_HOURS_FAB='lead_hours_fab', ALERT_SAFETY_HOURS='safety_hours', ALERT_WATCH_HOURS='watch_hours')
NAMES = ('history', 'schedule', 'observed')


def _num(value, name, lo, hi):
    if isinstance(value, bool) or value is None: raise ValueError(f'{name} must be a number.')
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{name} must be a number.') from None
    if not math.isfinite(n): raise ValueError(f'{name} must be a finite number.')
    if not lo <= n <= hi: raise ValueError(f'{name} must be between {lo} and {hi}.')
    return n


def default_settings(env=None):
    env = os.environ if env is None else env
    out = copy.deepcopy(DEFAULT_SETTINGS)
    for var, key in ENV.items():
        raw = env.get(var)
        if raw is None or str(raw).strip() == '': continue
        out[key] = _num(raw, var, *RANGES[key])
    return out


def validate_settings(current, changes):
    out = copy.deepcopy(current)
    if not isinstance(changes, dict): raise ValueError('Settings must be an object.')
    for key, value in changes.items():
        if key == 'weights':
            if not isinstance(value, dict): raise ValueError('weights must be an object.')
            for name, w in value.items():
                if name not in NAMES: raise ValueError(f'Unknown weight: {name}.')
                out['weights'][name] = _num(w, f'{name} weight', *WEIGHT_RANGE)
        elif key in RANGES: out[key] = _num(value, key, *RANGES[key])
        else: raise ValueError(f'Unknown setting: {key}.')
    return out


def _ok(rate):
    return rate is not None and not isinstance(rate, bool) and isinstance(rate, (int, float)) and math.isfinite(rate) and rate >= 0


def blend_rate(history, schedule, observed, weights, divergence_pct):
    rates = dict(history=history, schedule=schedule, observed=observed)
    live = {n: float(r) for n, r in rates.items() if (weights.get(n) or 0) > 0 and _ok(r)}
    dropped = []
    if 'history' in live and 'schedule' in live and live['history'] > 0 \
            and abs(live['schedule'] - live['history']) / live['history'] * 100 > divergence_pct:
        del live['history']; dropped.append('history')
    used = [n for n in NAMES if n in live]
    total = sum(weights[n] for n in used)
    return dict(rate=sum(weights[n] * live[n] for n in used) / total if used else None, used=used, dropped=dropped)


def project(items, inventory, settings, lead_classes=None, rule_status_key='status'):
    lead_classes = lead_classes or {}
    rows = {position_key(r.get('material'), r.get('storage_type'), r.get('bin')): r for r in inventory or []}
    weights, safety = settings['weights'], settings['safety_hours']
    for p in items:
        row = rows.get(position_key(p['material'], p['storage_type'], p['bin'])) or {}
        lead_class = lead_classes.get(p['key']) or 'stocked'
        if lead_class not in ('stocked', 'fab'): lead_class = 'stocked'
        lead = float(settings['lead_hours_fab' if lead_class == 'fab' else 'lead_hours_stocked'])
        planned = row.get('planned_qty_next_30d')
        schedule = planned / 30 if row.get('bom_status') == 'ok' and _ok(planned) else None
        inputs = dict(history=row.get('replen_qty_per_day'), schedule=schedule, observed=row.get('observed_depletion_per_day'))
        blend = blend_rate(inputs['history'], schedule, inputs['observed'], weights, settings['divergence_pct'])
        rate, status = blend['rate'], p[rule_status_key]
        p.update(lead_class=lead_class, lead_hours=lead, demand_per_day=rate, demand_basis=blend['used'], demand_dropped=blend['dropped'], demand_inputs=inputs,
                 hours_to_min=None, suggested_qty=None, forecast=None)
        if status in ('No tolerance', 'No stock'): continue
        if status == 'Alert':
            p['forecast'], p['hours_to_min'] = 'below_min', 0
        elif not rate:
            p['forecast'] = 'no_rate'; continue
        else:
            hours = max(0, p['available'] - p['min']) / rate * 24
            p['hours_to_min'] = round(hours, 2)
            if hours <= lead + safety: p['forecast'], p[rule_status_key] = 'within_lead', 'Predicted'
            elif hours <= settings['watch_hours']: p['forecast'] = 'watch'
            else: p['forecast'] = 'ok'
        if p['max'] is not None and p['max'] > 0 and (rate is not None or p['forecast'] == 'below_min'):
            p['suggested_qty'] = max(0, math.ceil(p['max'] - (p['available'] - (rate or 0) * lead / 24)))
        if p['forecast'] == 'ok': p['suggested_qty'] = None
    return items
