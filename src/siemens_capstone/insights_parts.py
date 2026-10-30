"""Pure, descriptive refill diagnostics. Transfer targets are not measured receipts."""
import csv
from io import StringIO
import math
from statistics import median

from .alerts import position_key

# min_refills: a short-refill flag needs at least this many refills, so one transfer cannot decide it.
DEFAULT_THRESHOLDS = dict(refill_size_pct=50.0, refills_30d=8, divergence_pct=50.0, min_refills=3)
THRESHOLD_RANGES = dict(refill_size_pct=(0, 100), refills_30d=(0, 100), divergence_pct=(0, 1000), min_refills=(1, 100))
NOTES = ['Refill size uses the transfer target quantity, not a measured receipt.',
         'Days under minimum is pending until historical bin status is available.']
CSV_FIELDS = ('key', 'material', 'description', 'supermarket', 'storage_type', 'bin', 'min', 'max',
              'refills_30d', 'median_refill_pct', 'share_refills_95', 'history_per_day', 'schedule_per_day',
              'divergence_pct', 'days_under_min', 'days_under_min_status', 'flags')


def validate_thresholds(changes=None):
    out = DEFAULT_THRESHOLDS.copy()
    if changes is None: return out
    if not isinstance(changes, dict): raise ValueError('Thresholds must be an object.')
    for key, value in changes.items():
        if key not in THRESHOLD_RANGES: raise ValueError(f'Unknown threshold: {key}.')
        lo, hi = THRESHOLD_RANGES[key]
        try:
            if isinstance(value, bool): raise ValueError()
            number = float(value)
            if not math.isfinite(number) or not lo <= number <= hi: raise ValueError()
        except (ValueError, TypeError):
            raise ValueError(f'{key} must be a number between {lo} and {hi}.') from None
        out[key] = number
    return out


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def part_metrics(position, inventory_row, thresholds):
    thresholds = validate_thresholds(thresholds)
    row = inventory_row or {}
    maximum = position.get('max')
    refills = [dict(date=r['date'], quantity=r['quantity'],
                    pct_of_max=r['quantity'] / maximum * 100 if _finite(maximum) and maximum > 0 else None)
               for r in sorted(row.get('window_refills') or [], key=lambda r: r['date'])]
    pcts = [r['pct_of_max'] for r in refills if r['pct_of_max'] is not None]
    med = median(pcts) if pcts else None
    history, planned = row.get('replen_qty_per_day'), row.get('planned_qty_next_30d')
    history = history if _finite(history) else None
    schedule = planned / 30 if row.get('bom_status') == 'ok' and _finite(planned) else None
    divergence = abs(schedule - history) / history * 100 if schedule is not None and history is not None and history > 0 else None
    flags = []
    if med is not None and len(pcts) >= thresholds['min_refills'] and med < thresholds['refill_size_pct']: flags.append('short_refills')
    if len(refills) > thresholds['refills_30d']: flags.append('frequent_refills')
    if divergence is not None and divergence > thresholds['divergence_pct']: flags.append('divergent')
    result = {key: position.get(key) for key in CSV_FIELDS[:8]}
    result['key'] = position_key(position.get('material'), position.get('storage_type'), position.get('bin'))
    result.update(refills_30d=len(refills), refills=refills, median_refill_pct=med,
                  share_refills_95=sum(p >= 95 for p in pcts) / len(pcts) if pcts else None,
                  history_per_day=history, schedule_per_day=schedule, divergence_pct=divergence,
                  days_under_min=None, days_under_min_status='pending', flags=flags)
    return result


def parts_report(positions, inventory, thresholds=None):
    thresholds = validate_thresholds(thresholds)
    rows = {position_key(r.get('material'), r.get('storage_type'), r.get('bin')): r for r in inventory or []}
    parts = [part_metrics(p, rows.get(position_key(p.get('material'), p.get('storage_type'), p.get('bin'))), thresholds) for p in positions]
    pcts = [r['pct_of_max'] for p in parts for r in p['refills'] if r['pct_of_max'] is not None]
    summary = dict(positions=len(parts), flagged=sum(bool(p['flags']) for p in parts),
                   median_refill_pct=median(pcts) if pcts else None,
                   share_refills_95=sum(p >= 95 for p in pcts) / len(pcts) if pcts else None)
    summary.update({flag: sum(flag in p['flags'] for p in parts) for flag in ('short_refills', 'frequent_refills', 'divergent')})
    return dict(thresholds=thresholds, summary=summary, notes=list(NOTES), parts=parts)


def to_csv(report):
    output = StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for part in report['parts']:
        row = {key: part.get(key) for key in CSV_FIELDS}
        row['flags'] = ';'.join(part['flags'])
        writer.writerow(row)
    return output.getvalue()
