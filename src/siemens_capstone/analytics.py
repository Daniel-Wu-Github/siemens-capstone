"""Descriptive workbook analytics and explicitly assumption-based scenarios.

Source events never become depletion labels. Quantities are linked to a stock
position only when material, destination bin, and storage type are unambiguous.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from hashlib import sha256
import math
import json
from statistics import median

from .ingest import excel_datetime, identifier

ANALYSIS_VERSION = '2.1'


def numeric(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def local_date(value, system):
    if value is None: return None
    try: return datetime.fromisoformat(excel_datetime(value, 0, system))
    except (ValueError, TypeError): return None


def label(value):
    return str(value).strip() if value is not None else ''


def range_of(dates):
    return {'start': min(dates).isoformat(), 'end': max(dates).isoformat()} if dates else {'start': None, 'end': None}


SUPERMARKETS = {'401': 'Silver', '402': 'Green', '403': 'Blue', '404': 'Purple', '405': 'Yellow', '406': 'Closing', '407': 'Brown', '408': 'Orange'}
STATUS_CODES = {'10': 'Order received, not released', '11': 'Processing at workstation', '20': 'Released, not started', '30': 'Idle at workstation',
                '40': 'Rework', '45': 'PIT', '70': 'Production complete', '99': 'NA', '200': 'NA'}
CATEGORIES = ('replenishment', 'return_to_warehouse', 'supermarket_transfer', 'other')
HORIZON_DEFAULT_HOURS = 8


def storage_code(value):
    s = identifier(value)
    return s.zfill(3) if s.isdigit() else s


def transfer_category(row):
    if 'Source Storage Type' not in row: return 'replenishment'  # older extracts carry no source column
    src, dest = storage_code(row.get('Source Storage Type')), storage_code(row.get('Dest. Storage Type'))
    if src in ('001', '011') and dest.startswith('4'): return 'replenishment'
    if src.startswith('4') and dest == '001': return 'return_to_warehouse'
    if src.startswith('4') and dest.startswith('4'): return 'supermarket_transfer'
    return 'other'


def supermarket(storage_type):
    code = storage_code(storage_type)
    return SUPERMARKETS.get(code) or (f'Type {code}' if code.startswith('4') else code)


def part_norm(value):
    s = label(value)
    return s[4:].strip() if s.upper().startswith('US2:') else s


def monday(day):
    return (day - timedelta(days=day.weekday())).isoformat()


def tolerance_for(row):
    lo, hi = (numeric(row.get(k)) for k in ('Min', 'Max')) if row else (None, None)
    if lo is None or hi is None: return None, None, 'missing'
    # Same definitions as the Stock alerts page (alerts.positions).
    if hi == 0 and lo > 0: return lo, hi, 'max_not_set'
    if lo > hi: return lo, hi, 'invalid'
    return lo, hi, 'zero_min' if lo == 0 else 'ok'


def snapshot_stocks(rows):
    stocks, seen = {}, set()
    for row in rows:
        material, bin_ = label(row.get('Material')), label(row.get('Storage Bin'))
        stock = numeric(row.get('Available stock'))
        if not material or not bin_ or stock is None: continue
        typ = label(row.get('Storage Type'))
        segment = (material, typ, bin_, label(row.get('Storage Unit')), label(row.get('Special Stock Number')))
        if segment in seen: continue
        seen.add(segment)
        stocks[(material, bin_, typ)] = stocks.get((material, bin_, typ), 0) + stock
    return stocks


def observed_depletion(history):
    drops = []
    for (d1, s1), (d2, s2) in zip(history, history[1:]):
        days = (datetime.fromisoformat(d2).date() - datetime.fromisoformat(d1).date()).days
        if days > 0 and s2 < s1: drops.append((s1-s2)/days)
    return sum(drops)/len(drops) if drops else 0.0


def analyze(records, date_system='1900', references=None):
    references = references or {}
    tolerances, bom_rows = references.get('tolerances'), references.get('bom')
    snapshots = {s['as_of']: s['rows'] for s in sorted(references.get('snapshots') or [], key=lambda s: s['as_of'])}
    bins = records.get('BIN_STATUS_EXAMPLE', [])
    positions = {}
    stock_segments = {}
    duplicate_stock_rows = missing_stock_keys = 0
    for row in bins:
        key = tuple(label(row.get(k)) for k in ('Material', 'Storage Type', 'Storage Bin', 'Base Unit of Measure'))
        if not key[0] or not key[2]: missing_stock_keys += 1; continue
        if key not in positions:
            positions[key] = dict(id=sha256('|'.join(key).encode()).hexdigest()[:16], material=key[0],
                                  name=label(row.get('Material Description')) or key[0], storage_type=key[1], bin=key[2],
                                  supermarket=supermarket(key[1]) if key[1] else '',
                                  unit=key[3], stock=0, source_rows=0, invalid_stock_rows=0,
                                  window_transfer_quantity=0, window_transfer_events=0,
                                  target_quantity_per_day=None, stock_to_target_days=None)
        position = positions[key]; position['source_rows'] += 1
        stock = numeric(row.get('Available stock'))
        segment = key + (label(row.get('Storage Unit')), label(row.get('Special Stock Number')))
        if segment in stock_segments:
            duplicate_stock_rows += 1
            if stock_segments[segment] != stock: position['invalid_stock_rows'] += 1
            continue
        stock_segments[segment] = stock
        if stock is None: position['invalid_stock_rows'] += 1
        else: position['stock'] += stock
    for position in positions.values():
        if position['invalid_stock_rows']: position['stock'] = None

    transfers, duplicates, invalid_times, missing_dates, invalid_quantities, missing_keys = [], 0, 0, 0, 0, 0
    grouped_transfers = defaultdict(list)
    for row in records.get('HISTORICAL_SM_REPLENISHMENTS', []):
        key = (identifier(row.get('Transfer Order Number')), identifier(row.get('Transfer order item')))
        if not all(key): missing_keys += 1; continue
        grouped_transfers[key].append(row)
    conflicts = 0
    for group in grouped_transfers.values():
        duplicates += len(group)-1
        if len({json.dumps(r, sort_keys=True, default=str) for r in group}) > 1:
            conflicts += 1; continue
        row = group[0]
        start = local_date(row.get('_created_at_local'), date_system)
        end = local_date(row.get('_confirmed_at_local'), date_system)
        if start is None or end is None: missing_dates += 1; continue
        if end < start: invalid_times += 1; continue
        qty = numeric(row.get('Dest.target quantity'))
        if qty is None or qty <= 0: invalid_quantities += 1; continue
        transfers.append(dict(material=label(row.get('Material')), bin=label(row.get('Dest.Storage Bin')),
                              storage_type=label(row.get('Dest. Storage Type')), quantity=qty,
                              confirmed=end, hours=(end-start).total_seconds()/3600, category=transfer_category(row)))
    categories = {c: sum(e['category'] == c for e in transfers) for c in CATEGORIES}
    replen = [e for e in transfers if e['category'] == 'replenishment']
    transfer_dates = [r['confirmed'] for r in transfers]
    replen_dates = [r['confirmed'] for r in replen]
    latest = max(replen_dates).date() if replen_dates else None
    earliest = min(replen_dates).date() if replen_dates else None
    window_days = min(30, (latest-earliest).days+1) if latest else 0
    cutoff = latest - timedelta(days=window_days-1) if latest else None
    lookup = defaultdict(list)
    for p in positions.values(): lookup[(p['material'], p['bin'])].append(p)
    weekly, turnaround, frequent = Counter(), [e['hours'] for e in transfers], Counter()
    matched_events = ambiguous_events = unmatched_events = 0
    window_events = 0
    for event in replen:
        weekly[monday(event['confirmed'].date())] += 1
        if event['confirmed'].date() < cutoff: continue
        window_events += 1
        frequent[event['material']] += 1
        candidates = lookup[(event['material'], event['bin'])]
        if event['storage_type']:
            candidates = [p for p in candidates if p['storage_type'] == event['storage_type']]
        if not candidates: unmatched_events += 1; continue
        if len(candidates) != 1: ambiguous_events += 1; continue
        matched_events += 1
        p = candidates[0]
        p['window_transfer_quantity'] += event['quantity']
        p['window_transfer_events'] += 1

    production_rows = records.get('HISTORICAL_PROD_DATA', [])
    production_dates, monthly = [], defaultdict(lambda: dict(events=0, passed=0, held=0, orders=set()))
    statuses, stations = Counter(), defaultdict(lambda: dict(events=0, held=0, durations=[]))
    orders, order_status_counts = set(), {}
    order_info = {}
    for row in production_rows:
        status = label(row.get('statusstringDescription')) or 'Unknown'
        statuses[status] += 1
        order = identifier(row.get('productionorderOrdNr'))
        if order: orders.add(order)
        code = identifier(row.get('productionorderStatus'))
        if code:
            entry = order_status_counts.setdefault(code, dict(label=STATUS_CODES.get(code, f'Status {code}'), rows=0)); entry['rows'] += 1
        at = local_date(row.get('TransactionTime'), date_system)
        if order:
            info = order_info.setdefault(order, dict(done=False, item='', produced=None, transaction=None))
            info['done'] = info['done'] or code == '70'
            info['item'] = info['item'] or identifier(row.get('productionorderSalesOrderLineItem'))
            produced = local_date(row.get('productionorderProducedTime'), date_system)
            if produced and (info['produced'] is None or produced > info['produced']): info['produced'] = produced
            if at and (info['transaction'] is None or at > info['transaction']): info['transaction'] = at
        if at:
            production_dates.append(at); m = monthly[at.strftime('%Y-%m')]
            m['events'] += 1; m['passed'] += status.lower() == 'passed'; m['held'] += status.lower() == 'on hold'
            if order: m['orders'].add(order)
        station = label(row.get('stationDescr')) or 'Unknown station'
        workcenter = label(row.get('workcenterWorkCenter'))
        s = stations[(station, workcenter)]; s['events'] += 1; s['held'] += status.lower() == 'on hold'
        duration = numeric(row.get('JobTraceTime (MIN)'))
        if status.lower() == 'passed' and duration is not None and duration >= 0: s['durations'].append(duration)
    station_rows = [dict(name=key[0], workcenter=key[1], events=v['events'], held_events=v['held'],
                         hold_share=v['held']/v['events'], median_passed_minutes=median(v['durations']) if v['durations'] else None)
                    for key,v in stations.items()]
    station_rows.sort(key=lambda r:-r['events'])
    completed = [(i['item'], (i['produced'] or i['transaction']).date()) for i in order_info.values() if i['done'] and (i['produced'] or i['transaction'])]
    completed_weekly, completed_daily = Counter(), Counter()
    for _, day in completed: completed_weekly[monday(day)] += 1; completed_daily[day.isoformat()] += 1
    bom_window = min(30, (max(d for _, d in completed)-min(d for _, d in completed)).days+1) if completed else 0
    bom_cutoff = max(d for _, d in completed) - timedelta(days=bom_window-1) if completed else None

    # BOM: item -> {normalized part: qty per unit}. Estimates only; one example BOM.
    bom = defaultdict(lambda: defaultdict(float))
    for row in bom_rows or []:
        item, part, qty = identifier(row.get('Item')), part_norm(row.get('Part')), numeric(row.get('Qty'))
        if item and part and qty is not None: bom[item][part] += qty
    bom_parts = {part for parts in bom.values() for part in parts}
    order_counts = Counter(item for item, day in completed if item in bom and day >= bom_cutoff)
    bom_rate = defaultdict(float)
    for item, n in order_counts.items():
        for part, qty in bom[item].items(): bom_rate[part] += n*qty/bom_window

    schedule_rows = records.get('PLANNED_PROD_SCHEDULE', [])
    schedule_dates, schedule_months, scheduled_keys = [], Counter(), set()
    dated_lines = []
    schedule_covered = 0
    for row in schedule_rows:
        item = identifier(row.get('Line Item'))
        covered = item in bom; schedule_covered += covered
        dt = local_date(row.get('_start_at_local'), date_system)
        if not dt: continue
        schedule_dates.append(dt); schedule_months[dt.strftime('%Y-%m')] += 1
        dated_lines.append((item, dt.date()))
        key = (identifier(row.get('Sales Order')), identifier(row.get('Line Item')), dt.date().isoformat())
        if all(key): scheduled_keys.add(key)
    schedule_weekly = defaultdict(lambda: dict(lines=0, bom_lines=0))
    planned_by_part = defaultdict(float)
    horizon_end = min(d for _, d in dated_lines) + timedelta(days=30) if dated_lines else None
    for item, day in dated_lines:
        w = schedule_weekly[monday(day)]; w['lines'] += 1; w['bom_lines'] += item in bom
        if item in bom and day < horizon_end:
            for part, qty in bom[item].items(): planned_by_part[part] += qty

    part_positions = Counter(part_norm(p['material']) for p in positions.values())
    snapshot_data = {as_of: snapshot_stocks(rows) for as_of, rows in snapshots.items()}
    tolerance_index = {(label(r.get('Part')), label(r.get('Location'))): r for r in tolerances or []}
    for p in positions.values():
        p['window_days'] = window_days
        if p['window_transfer_events'] and p['unit'] in ('PC', 'EA') and window_days:
            rate = p['window_transfer_quantity']/window_days
            p['target_quantity_per_day'] = rate
            if p['stock'] is not None and p['stock'] >= 0:
                p['stock_to_target_days'] = round(p['stock']/rate, 4)
        p['scenario_eligible'] = p['stock_to_target_days'] is not None
        p['status'] = ('Unknown stock' if p['stock'] is None else 'Snapshot empty' if p['stock'] == 0 else
                       'Negative balance' if p['stock'] < 0 else 'No matched activity' if not p['window_transfer_events'] else 'Snapshot stock')
        p['replen_qty_per_day'] = p['target_quantity_per_day']
        lo, hi, flag = tolerance_for(tolerance_index.get((p['material'], p['bin'])))
        stock = p['stock']
        p.update(min=lo, max=hi, tolerance_flag=flag,
                 fill_pct=stock/hi*100 if stock is not None and hi and hi > 0 else None,
                 # min 0 is a real minimum: an empty bin is at its minimum (user decision 2026-10-09)
                 at_or_below_min=bool(lo is not None and flag != 'missing' and stock is not None and stock <= lo))
        norm = part_norm(p['material'])
        if norm not in bom_parts: p['bom_status'], p['bom_qty_per_day'], p['planned_qty_next_30d'] = 'not_in_bom', None, None
        elif part_positions[norm] > 1: p['bom_status'], p['bom_qty_per_day'], p['planned_qty_next_30d'] = 'shared', None, None
        else: p['bom_status'], p['bom_qty_per_day'], p['planned_qty_next_30d'] = 'ok', bom_rate.get(norm, 0.0), planned_by_part.get(norm, 0.0)
        floor = lo or 0
        def days(rate):
            if stock is None or not rate: return None
            return 0 if stock <= floor else round((stock-floor)/rate, 4)
        p['days_to_min_replen'] = days(p['replen_qty_per_day'])
        p['days_to_min_bom'] = days(p['bom_qty_per_day'])
        history = []
        for as_of, data in snapshot_data.items():
            value = data.get((p['material'], p['bin'], p['storage_type']), data.get((p['material'], p['bin'], '')))
            if value is not None: history.append((as_of, value))
        p['snapshot_history'] = [dict(as_of=a, stock=v) for a, v in history]
        p['observed_depletion_per_day'] = observed_depletion(history) if len(history) >= 2 else None
    inventory = sorted(positions.values(), key=lambda p: (not p['at_or_below_min'], p['days_to_min_replen'] is None,
                       p['days_to_min_replen'] or 0, p['material'], p['bin']))
    covered_orders = sum(1 for i in order_info.values() if i['item'] in bom)
    return dict(version=ANALYSIS_VERSION, forecast_validated=False, snapshot_as_of=None, horizon_default_hours=HORIZON_DEFAULT_HOURS,
                inventory=inventory, inventory_summary=dict(positions=len(inventory), materials=len({p['material'] for p in inventory}),
                    source_rows=len(bins), duplicate_rows=duplicate_stock_rows, missing_key_rows=missing_stock_keys,
                    empty_positions=sum(p['stock'] == 0 for p in inventory), invalid_positions=sum(p['stock'] is None or p['stock'] < 0 for p in inventory),
                    proxy_eligible_positions=sum(p['scenario_eligible'] for p in inventory)),
                references=dict(
                    tolerances=dict(loaded=tolerances is not None, rows=len(tolerances or []), matched_positions=sum(p['tolerance_flag'] != 'missing' for p in inventory),
                        zero_min=sum(p['tolerance_flag'] == 'zero_min' for p in inventory), invalid=sum(p['tolerance_flag'] == 'invalid' for p in inventory),
                        max_not_set=sum(p['tolerance_flag'] == 'max_not_set' for p in inventory)),
                    bom=dict(loaded=bom_rows is not None, rows=len(bom_rows or []), line_items=len(bom), parts=len(bom_parts),
                        production_orders_covered=covered_orders, production_orders_total=len(order_info),
                        schedule_rows_covered=schedule_covered, schedule_rows_total=len(schedule_rows)),
                    snapshots=[dict(as_of=a, rows=len(r)) for a, r in snapshots.items()]),
                transfers=dict(source_rows=len(records.get('HISTORICAL_SM_REPLENISHMENTS', [])), valid_events=len(transfers),
                    categories=categories,
                    duplicate_events=duplicates, invalid_time_order=invalid_times, missing_dates=missing_dates,
                    conflicting_duplicate_keys=conflicts,
                    invalid_quantities=invalid_quantities, missing_keys=missing_keys,
                    median_confirmation_hours=median(turnaround) if turnaround else None,
                    p90_confirmation_hours=sorted(turnaround)[max(0,math.ceil(len(turnaround)*.9)-1)] if turnaround else None,
                    zero_duration_events=sum(h==0 for h in turnaround), date_range=range_of(transfer_dates),
                    window_days=window_days, window_start=cutoff.isoformat() if cutoff else None, window_end=latest.isoformat() if latest else None,
                    window_events=window_events, matched_events=matched_events, ambiguous_events=ambiguous_events, unmatched_events=unmatched_events,
                    weekly=[dict(period=k,count=v) for k,v in sorted(weekly.items())],
                    frequent_materials=[dict(material=k,events=v) for k,v in frequent.most_common(10)]),
                production=dict(events=len(production_rows), distinct_orders=len(orders), passed_events=sum(n for status,n in statuses.items() if status.lower() == 'passed'),
                    held_events=sum(n for status,n in statuses.items() if status.lower() == 'on hold'), undated_events=len(production_rows)-len(production_dates),
                    statuses=dict(statuses), date_range=range_of(production_dates), stations=station_rows[:10],
                    monthly=[dict(period=k,events=v['events'],passed=v['passed'],held=v['held'],orders=len(v['orders'])) for k,v in sorted(monthly.items())],
                    completed_orders_weekly=[dict(period=k,orders=v) for k,v in sorted(completed_weekly.items())],
                    completed_orders_daily=[dict(period=k,orders=v) for k,v in sorted(completed_daily.items())],
                    order_status_counts=order_status_counts, status_codes=STATUS_CODES, bom_window_days=bom_window),
                schedule=dict(source_rows=len(schedule_rows), dated_rows=len(schedule_dates), unique_lines_by_date=len(scheduled_keys),
                    date_range=range_of(schedule_dates), monthly=[dict(period=k,count=v) for k,v in sorted(schedule_months.items())],
                    weekly=[dict(period=k,lines=v['lines'],bom_lines=v['bom_lines']) for k,v in sorted(schedule_weekly.items())]),
                assumptions=[
                    'The bin snapshot has no verified as-of timestamp. Empty means zero available stock in that snapshot, not a live factory stockout.',
                    'Transfer confirmation time is a recorded system interval, not validated physical warehouse lead time; zero intervals may reflect automatic confirmation.',
                    'Dest.target quantity is a transfer target, not actual received quantity or consumption. Only transfers from warehouse storage types (001, 011) into a supermarket (4xx) count as replenishments; returns and supermarket-to-supermarket moves are excluded from rates.',
                    'Transfers do not declare a unit. The proxy assumes destination quantities use the matched bin count unit (PC or EA); confirm this before operational use.',
                    'Production rows are station events, not finished units. BOM-implied demand is an estimate: completed orders per line item times BOM quantity over the recent window, from one example BOM that may not cover every order. Planned rows are sales-order lines, not validated demand.',
                    'The rate averages over calendar days ending at the latest valid transfer, including quiet days and potentially partial endpoint days. No shift calendar is available.',
                    'Min/Max tolerances come from the supplied tolerance file; zero minimums and rows with min at or above max are flagged, not corrected. Days to minimum is a descriptive estimate, not a stockout prediction.',
                ])
