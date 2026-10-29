"""Clean -> model -> alert -> dashboard pipeline described in the final report.

Each stage is a pure function over plain row dictionaries so the report's test
tables (UT, IT, ST, VT) can target one behaviour at a time. Nothing here reads
files or the network; loaders take bytes or already-parsed rows.

Cleaning never silently drops a row: every removed row is returned with a
reason, so `rows in == rows kept + rows removed` always holds (IT-01).
"""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from statistics import mean
import math

from .analytics import part_norm, storage_code, supermarket, tolerance_for, transfer_category
from .ingest import identifier

# Storage types that represent the central warehouse; everything 4xx is a supermarket.
WAREHOUSE_TYPES = ('001', '011')
# Stations that build product. Everything else (shipping, palletization) is out of scope.
STATION_PREFIXES = ('main install', 'distribution')
# Job trace times below this are machine noise, not real work (13,241 of 75,554 real rows).
NEAR_ZERO_MINUTES = 0.1
# Production order status meaning "production complete".
COMPLETED_STATUS = '70'

REMOVAL_REASONS = {
    'reverse_transfer': 'reverse transfer: supermarket back to warehouse',
    'supermarket_transfer': 'supermarket-to-supermarket move, not a warehouse replenishment',
    'other_transfer': 'neither source nor destination is a warehouse-to-supermarket move',
    'out_of_scope_station': 'station is not an install or distribution station',
    'near_zero_time': f'job trace time below {NEAR_ZERO_MINUTES} minutes is machine noise',
    'negative_time': 'job trace time is negative',
    'missing_time': 'job trace time is missing or not numeric',
    'missing_key': 'row lacks a required identifier',
    'invalid_quantity': 'destination quantity is missing, zero, or negative',
    'invalid_date': 'creation or confirmation date could not be read',
    'time_order': 'confirmation precedes creation',
    'tolerance_invalid': 'minimum is greater than maximum',
    'tolerance_missing': 'minimum or maximum is not set',
}


def _removed(row, code):
    return dict(row=row, code=code, reason=REMOVAL_REASONS[code])


def numeric(value):
    """Float, or None when the value is blank, boolean, or not a finite number."""
    if value is None or value == '' or isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


# --------------------------------------------------------------------------- cleaning


def clean_replenishments(rows):
    """UT-01: keep warehouse -> supermarket transfers; remove reverse and lateral moves."""
    kept, removed = [], []
    for row in rows:
        category = transfer_category(row)
        if category == 'replenishment':
            kept.append(row)
        elif category == 'return_to_warehouse':
            removed.append(_removed(row, 'reverse_transfer'))
        elif category == 'supermarket_transfer':
            removed.append(_removed(row, 'supermarket_transfer'))
        else:
            removed.append(_removed(row, 'other_transfer'))
    return dict(rows_in=len(rows), kept=kept, removed=removed)


def is_build_station(name):
    text = str(name or '').strip().lower()
    return any(text.startswith(prefix) for prefix in STATION_PREFIXES)


def clean_stations(rows, field='stationDescr'):
    """UT-02: keep only install and distribution stations."""
    kept, removed = [], []
    for row in rows:
        if is_build_station(row.get(field)):
            kept.append(row)
        else:
            removed.append(_removed(row, 'out_of_scope_station'))
    return dict(rows_in=len(rows), kept=kept, removed=removed)


def clean_job_times(rows, field='JobTraceTime (MIN)', threshold=NEAR_ZERO_MINUTES):
    """UT-03: flag near-zero job times and leave them out of time averages."""
    kept, removed = [], []
    for row in rows:
        value = numeric(row.get(field))
        if value is None:
            removed.append(_removed(row, 'missing_time'))
        elif value < 0:
            removed.append(_removed(row, 'negative_time'))
        elif value < threshold:
            removed.append(_removed(row, 'near_zero_time'))
        else:
            kept.append(row)
    return dict(rows_in=len(rows), kept=kept, removed=removed)


def average_job_minutes(rows, field='JobTraceTime (MIN)'):
    """Mean job time over rows that survived clean_job_times; None when nothing qualifies."""
    values = [numeric(r.get(field)) for r in rows]
    values = [v for v in values if v is not None]
    return mean(values) if values else None


def join_production_schedule(production, schedule):
    """UT-04: join on sales order + line item and report what did not match."""
    def prod_key(row):
        return (identifier(row.get('salesorderOrdNr')), identifier(row.get('productionorderSalesOrderLineItem')))

    def sched_key(row):
        return (identifier(row.get('Sales Order')), identifier(row.get('Line Item')))

    produced = defaultdict(list)
    for row in production:
        key = prod_key(row)
        if all(key):
            produced[key].append(row)
    matched, unmatched_schedule = [], []
    seen = set()
    for row in schedule:
        key = sched_key(row)
        if all(key) and key in produced:
            seen.add(key)
            matched.append(dict(key=key, schedule=row, production=produced[key]))
        else:
            unmatched_schedule.append(row)
    unmatched_production = [r for r in production if prod_key(r) not in seen or not all(prod_key(r))]
    return dict(matched=matched, unmatched_schedule=unmatched_schedule,
                unmatched_production=unmatched_production,
                schedule_rows=len(schedule), production_rows=len(production))


def completed_orders(rows):
    """UT-05: only a status-70 step completes a production order."""
    steps, orders = [], {}
    for row in rows:
        order = identifier(row.get('productionorderOrdNr'))
        status = identifier(row.get('productionorderStatus'))
        if not order:
            continue
        entry = orders.setdefault(order, dict(order=order, steps=0, completed=False))
        entry['steps'] += 1
        if status == COMPLETED_STATUS:
            entry['completed'] = True
            steps.append(row)
    done = [o for o in orders.values() if o['completed']]
    return dict(orders=orders, completed_orders=len(done), completed_steps=len(steps),
                completed_order_numbers=sorted(o['order'] for o in done))


def check_tolerances(rows):
    """UT-06: a part is usable only with a real minimum and maximum; others are skipped."""
    usable, skipped = [], []
    for row in rows:
        low, high, flag = tolerance_for(row)
        entry = dict(part=part_norm(row.get('Part')), location=identifier(row.get('Location')),
                     min=low, max=high, flag=flag)
        if flag == 'invalid':
            skipped.append(dict(entry, reason=REMOVAL_REASONS['tolerance_invalid']))
        elif flag in ('missing', 'max_not_set'):
            skipped.append(dict(entry, reason=REMOVAL_REASONS['tolerance_missing']))
        else:
            usable.append(entry)
    return dict(rows_in=len(rows), usable=usable, skipped=skipped)


# --------------------------------------------------------------------------- model


def replenishment_rate(replenishments, products_built):
    """UT-07: replenishments per product built."""
    count = numeric(replenishments)
    built = numeric(products_built)
    if count is None or built is None or built <= 0:
        raise ValueError('Products built must be a positive number.')
    return count / built


def hours_to_minimum(available, minimum, usage_per_hour):
    """UT-08: hours of cover before available stock reaches the minimum."""
    stock = numeric(available)
    floor = numeric(minimum)
    rate = numeric(usage_per_hour)
    if stock is None or floor is None or rate is None:
        return None
    if stock <= floor:
        return 0.0
    if rate <= 0:
        return None  # no observed use, so no finite crossing
    return (stock - floor) / rate


def alert_due(hours_to_min, lead_hours, buffer_hours=0.0):
    """UT-09: alert when the minimum is reached within the warehouse lead time plus buffer."""
    if hours_to_min is None:
        return False
    horizon = numeric(lead_hours) or 0.0
    horizon += numeric(buffer_hours) or 0.0
    return hours_to_min <= horizon


def suggested_quantity(maximum, available):
    """UT-12: bring the bin back up to its maximum."""
    high = numeric(maximum)
    stock = numeric(available)
    if high is None or stock is None:
        return None
    return max(0.0, high - stock)


BELOW_MINIMUM = 'Below Minimum'


def stock_status(available, minimum):
    """UT-11: status from available stock, never total stock."""
    stock = numeric(available)
    floor = numeric(minimum)
    if floor is None:
        return 'No tolerance'
    if stock is None:
        return 'No stock'
    return BELOW_MINIMUM if stock <= floor else 'OK'


# --------------------------------------------------------------------------- pipeline stages


def clean_all(records, tolerances=None):
    """IT-01: clean every source and reconcile row counts with a reason per removed row."""
    sources = {}
    replen = clean_replenishments(records.get('HISTORICAL_SM_REPLENISHMENTS', []))
    sources['HISTORICAL_SM_REPLENISHMENTS'] = replen
    production = records.get('HISTORICAL_PROD_DATA', [])
    stations = clean_stations(production)
    times = clean_job_times(stations['kept'])
    # Station filtering and time filtering are reported as one source, so the
    # reconciliation still balances against the original production row count.
    sources['HISTORICAL_PROD_DATA'] = dict(rows_in=len(production), kept=times['kept'],
                                           removed=stations['removed'] + times['removed'])
    schedule = records.get('PLANNED_PROD_SCHEDULE', [])
    sources['PLANNED_PROD_SCHEDULE'] = dict(rows_in=len(schedule), kept=schedule, removed=[])
    bins = records.get('BIN_STATUS_EXAMPLE', [])
    kept_bins, removed_bins = [], []
    for row in bins:
        if not identifier(row.get('Material')) or not identifier(row.get('Storage Bin')):
            removed_bins.append(_removed(row, 'missing_key'))
        else:
            kept_bins.append(row)
    sources['BIN_STATUS_EXAMPLE'] = dict(rows_in=len(bins), kept=kept_bins, removed=removed_bins)
    if tolerances is not None:
        checked = check_tolerances(tolerances)
        sources['TOLERANCES'] = dict(rows_in=checked['rows_in'], kept=checked['usable'],
                                     removed=[dict(row=s, code='tolerance', reason=s['reason'])
                                              for s in checked['skipped']])
    balanced = all(s['rows_in'] == len(s['kept']) + len(s['removed']) for s in sources.values())
    return dict(sources=sources, balanced=balanced,
                rows_in=sum(s['rows_in'] for s in sources.values()),
                rows_kept=sum(len(s['kept']) for s in sources.values()),
                rows_removed=sum(len(s['removed']) for s in sources.values()))


NOT_ENOUGH_HISTORY = 'not enough history'
MIN_EVENTS_FOR_RATE = 2


def build_model(cleaned, tolerances=None, window_days=None, products_built=None):
    """IT-02: every part gets a replenishment rate or is marked "not enough history"."""
    replen = cleaned['sources'].get('HISTORICAL_SM_REPLENISHMENTS', {}).get('kept', [])
    bins = cleaned['sources'].get('BIN_STATUS_EXAMPLE', {}).get('kept', [])
    tolerance_index = {}
    for row in tolerances or []:
        tolerance_index[(part_norm(row.get('Part')), identifier(row.get('Location')))] = row

    events = defaultdict(list)
    for row in replen:
        part = part_norm(row.get('Material'))
        bin_ = identifier(row.get('Dest.Storage Bin'))
        day = _row_date(row)
        quantity = numeric(row.get('Dest.target quantity'))
        if not part or not bin_ or day is None or quantity is None or quantity <= 0:
            continue
        events[(part, bin_)].append(dict(day=day, quantity=quantity,
                                         storage_type=storage_code(row.get('Dest. Storage Type'))))

    days = [e['day'] for group in events.values() for e in group]
    span = (max(days) - min(days)).days + 1 if days else 0
    window = window_days or span or 1

    stock_by_position = defaultdict(float)
    units, types = {}, {}
    for row in bins:
        key = (part_norm(row.get('Material')), identifier(row.get('Storage Bin')))
        value = numeric(row.get('Available stock'))
        if value is not None:
            stock_by_position[key] += value
        units.setdefault(key, identifier(row.get('Base Unit of Measure')))
        types.setdefault(key, storage_code(row.get('Storage Type')))

    parts = []
    for key in sorted(set(events) | set(stock_by_position)):
        part, bin_ = key
        group = events.get(key, [])
        tolerance = tolerance_index.get(key)
        low, high, flag = tolerance_for(tolerance)
        available = stock_by_position.get(key)
        # The bin snapshot is the authoritative position record; events are the fallback.
        storage_type = types.get(key) or (group[0]['storage_type'] if group else '')
        entry = dict(part=part, bin=bin_, storage_type=storage_type,
                     supermarket=supermarket(storage_type) if storage_type else '',
                     unit=units.get(key, ''), available=available, min=low, max=high,
                     tolerance_flag=flag, events=len(group),
                     quantity=sum(e['quantity'] for e in group))
        if len(group) >= MIN_EVENTS_FOR_RATE:
            entry['rate_per_day'] = entry['quantity'] / window
            entry['status'] = 'modelled'
        else:
            entry['rate_per_day'] = None
            entry['status'] = NOT_ENOUGH_HISTORY
        entry['stock_status'] = stock_status(available, low)
        entry['below_minimum'] = entry['stock_status'] == BELOW_MINIMUM
        entry['suggested_quantity'] = suggested_quantity(high, available)
        rate_per_hour = (entry['rate_per_day'] / 24) if entry['rate_per_day'] else None
        entry['hours_to_minimum'] = hours_to_minimum(available, low, rate_per_hour)
        parts.append(entry)

    modelled = [p for p in parts if p['status'] == 'modelled']
    result = dict(parts=parts, window_days=window, modelled=len(modelled),
                  not_enough_history=len(parts) - len(modelled))
    if products_built:
        result['replenishment_rate_per_product'] = replenishment_rate(len(replen), products_built)
    return result


WARNING = 'Warning'


def classify(part, lead_hours, buffer_hours=0.0):
    """Warning when the bin is below minimum or reaches it inside lead time plus buffer."""
    if part.get('below_minimum'):
        return WARNING
    return WARNING if alert_due(part.get('hours_to_minimum'), lead_hours, buffer_hours) else 'OK'


def alert_rows(model, lead_hours=3.0, buffer_hours=0.0):
    """IT-03: one alert row per part in Warning, carrying part, bin, and quantity."""
    rows = []
    for part in model['parts']:
        if classify(part, lead_hours, buffer_hours) != WARNING:
            continue
        rows.append(dict(part=part['part'], bin=part['bin'], supermarket=part['supermarket'],
                         quantity=part['suggested_quantity'], available=part['available'],
                         min=part['min'], max=part['max'],
                         hours_to_minimum=part['hours_to_minimum']))
    return rows


def dashboard_view(model, lead_hours=3.0, buffer_hours=0.0):
    """IT-04: the counts the dashboard shows."""
    parts = model['parts']
    warnings = [p for p in parts if classify(p, lead_hours, buffer_hours) == WARNING]
    by_supermarket = Counter(p['supermarket'] for p in parts if p['below_minimum'])
    return dict(summary=dict(positions=len(parts),
                             below_minimum=sum(p['below_minimum'] for p in parts),
                             warnings=len(warnings),
                             modelled=model['modelled'],
                             not_enough_history=model['not_enough_history']),
                by_supermarket=dict(by_supermarket),
                rows=sorted(parts, key=lambda p: (not p['below_minimum'],
                                                  p['hours_to_minimum'] is None,
                                                  p['hours_to_minimum'] or 0, p['part'])))


# --------------------------------------------------------------------------- configuration


DEFAULT_CONFIG = dict(lead_hours=3.0, buffer_hours=1.0, warn_days=7.0, rule='at_or_below')


def load_config(path=None):
    """ST-06: operational settings come from the local TOML file, never from code."""
    import tomllib
    config = dict(DEFAULT_CONFIG)
    if path is None:
        return config
    from pathlib import Path
    file = Path(path)
    if not file.exists():
        return config
    data = tomllib.loads(file.read_text())
    for key in DEFAULT_CONFIG:
        if key in data:
            config[key] = data[key]
    for key in ('lead_hours', 'warn_days'):
        value = numeric(config[key])
        if value is None or value <= 0:
            raise ValueError(f'{key} must be a positive number in {file}.')
        config[key] = value
    buffer_hours = numeric(config['buffer_hours'])   # a zero buffer is a valid choice
    if buffer_hours is None or buffer_hours < 0:
        raise ValueError(f'buffer_hours must be zero or a positive number in {file}.')
    config['buffer_hours'] = buffer_hours
    return config


# --------------------------------------------------------------------------- source checks


REQUIRED_SHEETS = ('HISTORICAL_PROD_DATA', 'PLANNED_PROD_SCHEDULE',
                   'HISTORICAL_SM_REPLENISHMENTS', 'BIN_STATUS_EXAMPLE')
SHEET_FILES = {
    'HISTORICAL_PROD_DATA': 'historical production data',
    'PLANNED_PROD_SCHEDULE': 'production schedule',
    'HISTORICAL_SM_REPLENISHMENTS': 'supermarket replenishment log',
    'BIN_STATUS_EXAMPLE': 'bin status snapshot',
}


def require_sources(records, filename='the workbook'):
    """ST-05: stop with a message naming the file that is missing."""
    missing = [name for name in REQUIRED_SHEETS if not records.get(name)]
    if missing:
        names = ', '.join(f'{SHEET_FILES[m]} ({m})' for m in missing)
        raise ValueError(f'Cannot run: required source is missing from {filename}: {names}.')
    return True


# --------------------------------------------------------------------------- validation support


def _row_date(row):
    """Confirmation date of a replenishment row, as a date."""
    for key in ('_confirmed_at_local', 'Confirmation Date'):
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return datetime.fromisoformat(str(value)).date()
        except (TypeError, ValueError):
            continue
    return None


def replenishment_events(records):
    """Warehouse -> supermarket transfers as (part, bin, supermarket, day, quantity)."""
    cleaned = clean_replenishments(records.get('HISTORICAL_SM_REPLENISHMENTS', []))
    events = []
    for row in cleaned['kept']:
        day = _row_date(row)
        quantity = numeric(row.get('Dest.target quantity'))
        part = part_norm(row.get('Material'))
        if day is None or quantity is None or quantity <= 0 or not part:
            continue
        events.append(dict(part=part, bin=identifier(row.get('Dest.Storage Bin')),
                           location=storage_code(row.get('Dest. Storage Type')),
                           day=day, quantity=quantity))
    return events


def backtest_rate(events, train_start, train_end, test_start, test_end, top_n=20, recent_days=30):
    """VT-01: train a daily rate, predict the test window, score against a moving average."""
    train = [e for e in events if train_start <= e['day'] <= train_end]
    test = [e for e in events if test_start <= e['day'] <= test_end]
    if not train:
        raise ValueError('No replenishment events fall inside the training window.')
    ranked = Counter(e['part'] for e in train)
    parts = [p for p, _ in ranked.most_common(top_n)]
    train_days = (train_end - train_start).days + 1
    test_days = (test_end - test_start).days + 1
    recent_start = train_end - timedelta(days=recent_days - 1)

    totals, recent, actual = Counter(), Counter(), Counter()
    for e in train:
        totals[e['part']] += e['quantity']
        if e['day'] >= recent_start:
            recent[e['part']] += e['quantity']
    for e in test:
        actual[e['part']] += e['quantity']

    rows = []
    for part in parts:
        baseline = totals[part] / train_days * test_days
        model = recent[part] / recent_days * test_days
        truth = actual[part]
        rows.append(dict(part=part, actual=truth, moving_average=baseline, model=model,
                         model_error=abs(model - truth) / truth * 100 if truth > 0 else None,
                         baseline_error=abs(baseline - truth) / truth * 100 if truth > 0 else None))
    scored = [r for r in rows if r['model_error'] is not None]
    return dict(parts=rows, scored=len(scored), skipped_zero_actual=len(rows) - len(scored),
                train_events=len(train), test_events=len(test),
                model_mape=mean(r['model_error'] for r in scored) if scored else None,
                baseline_mape=mean(r['baseline_error'] for r in scored) if scored else None,
                train_quantity=sum(totals[p] for p in parts),
                test_quantity=sum(actual[p] for p in parts))


MIN_REPLEN_DAYS = 6


def replay_alerts(events, warn_days, min_days=MIN_REPLEN_DAYS):
    """VT-02 and VT-03: replay replenishments in order and score alert timing.

    Each replenishment is treated as a low-stock event: the bin was refilled
    because it reached its minimum. For every event after the first two, the
    trailing mean gap predicts the next one using only earlier events, and an
    alert is raised `warn_days` around that prediction. There are no dated stock
    snapshots in the supplied extract, so this replays events, not snapshots.
    """
    positions = defaultdict(set)
    for e in events:
        positions[(e['part'], e['bin'], e['location'])].add(e['day'])
    recurring = {k: sorted(v) for k, v in positions.items() if len(v) >= min_days}
    on_time = late = raised = false = 0
    for days in recurring.values():
        gaps = []
        for index in range(1, len(days)):
            gap = (days[index] - days[index - 1]).days
            if gaps:
                predicted = days[index - 1] + timedelta(days=max(1, round(mean(gaps))))
                raised += 1
                if abs((days[index] - predicted).days) <= warn_days:
                    on_time += 1
                else:
                    late += 1
                    false += 1
            gaps.append(gap)
    events_scored = on_time + late
    return dict(positions=len(recurring), events=events_scored, alerts=raised,
                on_time=on_time, missed=late, false_alerts=false,
                on_time_pct=on_time / events_scored * 100 if events_scored else None,
                false_alert_pct=false / raised * 100 if raised else None,
                warn_days=warn_days)


def sponsor_top_parts(summary_rows):
    """VT-04: the sponsor's supermarket table as {supermarket code: [parts]}."""
    table, current = defaultdict(list), None
    for row in summary_rows:
        name = row.get('Supermarket')
        if name:
            current = str(name).strip()
        location = identifier(row.get('LOC'))
        part = part_norm(row.get('Part Number'))
        if not location or not part:
            continue
        table[location].append(dict(supermarket=current, part=part,
                                    replens=numeric(row.get('# of Replens'))))
    return dict(table)


def top_parts_by_supermarket(events, top_n=2):
    """Our most-replenished parts per supermarket, by event count."""
    counts = defaultdict(Counter)
    for e in events:
        counts[e['location']][e['part']] += 1
    return {loc: [dict(part=p, replens=n) for p, n in c.most_common(top_n)]
            for loc, c in counts.items()}
