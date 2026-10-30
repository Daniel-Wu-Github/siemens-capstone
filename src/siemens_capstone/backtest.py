"""Offline, aggregate-only validation of demand rates against held-out transfers."""
from collections import Counter, defaultdict
from datetime import timedelta
from statistics import median

from . import analytics, proactive
from .ingest import identifier

DISCLAIMER = 'Validates the daily demand rate on held-out transfers. It does not test stockout prediction.'


def metrics(pairs):
    """Pairs are (actual, prediction); zero/zero pairs do not enter metrics."""
    pairs = [(a, p) for a, p in pairs if p is not None and (a > 0 or p > 0)]
    errors = [abs(p - a) for a, p in pairs]
    percentages = [abs(p - a) / a * 100 for a, p in pairs if a > 0]
    return dict(n=len(pairs), mae=sum(errors) / len(pairs) if pairs else None,
                median_absolute_pct_error=median(percentages) if percentages else None,
                mean_bias=sum(p - a for a, p in pairs) / len(pairs) if pairs else None,
                share_within_50pct=sum(e <= 50 for e in percentages) / len(percentages) if percentages else None)


def _comparisons(actual, variants):
    keys = set(actual)
    for rates in variants.values():
        keys &= {k for k, value in rates.items()
                 if k in actual and value is not None and (actual[k] > 0 or value > 0)}
    return dict(positions=len(keys), variants={name: metrics([(actual[k], values[k]) for k in keys])
                                               for name, values in variants.items()})


def _blends(history, schedule):
    both = {k for k in history if schedule.get(k) is not None}
    result = {'history_only_when_both': {k: history[k] for k in both}}
    for name, history_weight, divergence in [('blend_current', 1, 50), ('blend_no_override', 1, 1000),
                                            ('blend_history_2to1', 2, 1000)]:
        result[name] = {k: proactive.blend_rate(history[k], schedule.get(k), None,
                                              dict(history=history_weight, schedule=1, observed=0), divergence)['rate']
                        for k in history}
    return result


def evaluate(records, date_system='1900', references=None):
    """Return aggregates only. Fixed 30-day periods include quiet calendar days."""
    references = references or {}
    transfers, quality = analytics.parsed_transfers(records, date_system)
    events = [e for e in transfers if e['category'] == 'replenishment']
    if not events:
        raise ValueError('No valid replenishment events with confirmed dates.')
    latest = max(e['confirmed'].date() for e in events)
    holdout_start, training_start = latest - timedelta(days=29), latest - timedelta(days=59)
    training_end = holdout_start - timedelta(days=1)
    # Use the canonical stock grouping, BOM ambiguity and normalization rules.
    inventory = analytics.analyze({'BIN_STATUS_EXAMPLE': records.get('BIN_STATUS_EXAMPLE', [])},
                                  date_system, references)['inventory']
    positions = {p['id']: p for p in inventory}
    lookup = defaultdict(list)
    for k, p in positions.items():
        lookup[p['material'], p['bin']].append(k)
    history, actual = dict.fromkeys(positions, 0.0), dict.fromkeys(positions, 0.0)
    matched = Counter()
    for e in events:
        day = e['confirmed'].date()
        if not training_start <= day <= latest:
            continue
        candidates = lookup[e['material'], e['bin']]
        if e['storage_type']:
            candidates = [k for k in candidates if positions[k]['storage_type'] == e['storage_type']]
        if len(candidates) != 1:
            matched['ambiguous_or_unmatched'] += 1
            continue
        rates = actual if day >= holdout_start else history
        rates[candidates[0]] += e['quantity'] / 30
        matched['holdout' if day >= holdout_start else 'training'] += 1
    bom = defaultdict(lambda: defaultdict(float))
    for row in references.get('bom') or []:
        item, part, qty = identifier(row.get('Item')), analytics.part_norm(row.get('Part')), analytics.numeric(row.get('Qty'))
        if item and part and qty is not None:
            bom[item][part] += qty
    scheduled, completed = Counter(), Counter()
    scheduled_lines = 0
    for row in records.get('PLANNED_PROD_SCHEDULE', []):
        at = analytics.local_date(row.get('_start_at_local'), date_system)
        if at and holdout_start <= at.date() <= latest:
            scheduled_lines += 1
            scheduled[identifier(row.get('Line Item'))] += 1
    for info in analytics.production_orders(records.get('HISTORICAL_PROD_DATA', []), date_system).values():
        at = info['produced'] or info['transaction']
        if info['done'] and at and holdout_start <= at.date() <= latest:
            completed[info['item']] += 1

    def expanded(counts):
        quantities = defaultdict(float)
        for item, n in counts.items():
            for part, qty in bom[item].items():
                quantities[part] += n * qty / 30
        return {k: quantities[analytics.part_norm(p['material'])] if p['bom_status'] == 'ok' else None
                for k, p in positions.items()}

    schedule, actual_bom = expanded(scheduled), expanded(completed)
    variants = dict(history=history, bom_actual=actual_bom)
    if scheduled_lines:
        variants.update(schedule=schedule, **_blends(history, schedule))
    report = dict(disclaimer=DISCLAIMER,
                  training=dict(start=training_start.isoformat(), end=training_end.isoformat(), days=30),
                  holdout=dict(start=holdout_start.isoformat(), end=latest.isoformat(), days=30),
                  schedule_status='testable' if scheduled_lines else 'schedule not testable on this extract',
                  labels={'bom_actual': 'perfect-foresight upper bound'},
                  variants={name: metrics([(actual[k], rates.get(k)) for k in actual]) for name, rates in variants.items()},
                  common_subset=_comparisons(actual, variants),
                  coverage=dict(positions=len(positions), matched_events=dict(matched), transfer_quality=quality),
                  notes=['Actual means transfer target quantity / 30, not measured consumption.',
                         'Planned schedule availability before the holdout is assumed; this extract has no schedule revision timestamps.',
                         'The snapshot defines the position universe; historical position membership is unavailable.',
                         'The 1000% divergence setting follows the requested no-override variant; larger divergence still invokes the locked override.'])
    if not scheduled_lines:
        foresight = dict(history=history, bom_actual=actual_bom, **_blends(history, actual_bom))
        report['perfect_foresight'] = dict(label='with perfect-foresight schedule',
            variants={name: metrics([(actual[k], rates.get(k)) for k in actual]) for name, rates in foresight.items()},
            common_subset=_comparisons(actual, foresight))
    return report


def to_markdown(report):
    lines = [DISCLAIMER, '', f"Training: {report['training']['start']} to {report['training']['end']} (30 days).",
             f"Holdout: {report['holdout']['start']} to {report['holdout']['end']} (30 days).", '', report['schedule_status'], '']

    def table(title, variants):
        lines.extend([f'## {title}', '', '| Variant | n | MAE (units/day) | Median absolute % error | Mean bias | Share within ±50% |',
                      '| --- | ---: | ---: | ---: | ---: | ---: |'])
        for name, m in variants.items():
            label = name + (' (perfect-foresight upper bound)' if name == 'bom_actual' else '')
            values = ['—' if m[k] is None else f'{m[k]:.6g}' for k in
                      ('n', 'mae', 'median_absolute_pct_error', 'mean_bias', 'share_within_50pct')]
            lines.append('| ' + ' | '.join([label] + values) + ' |')
        lines.append('')

    table('Available positions per variant', report['variants'])
    table('Common subset', report['common_subset']['variants'])
    if 'perfect_foresight' in report:
        section = report['perfect_foresight']
        table(section['label'], section['variants'])
        table(section['label'] + ' — common subset', section['common_subset']['variants'])
    lines.extend(report['notes'])
    return '\n'.join(lines) + '\n'
