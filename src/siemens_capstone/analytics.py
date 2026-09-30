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

from .engine import number
from .ingest import excel_datetime, identifier

ANALYSIS_VERSION = '1.3'


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


def analyze(records, date_system='1900'):
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
                              confirmed=end, hours=(end-start).total_seconds()/3600))
    transfer_dates = [r['confirmed'] for r in transfers]
    earliest = min(transfer_dates).date() if transfer_dates else None
    latest = max(transfer_dates).date() if transfer_dates else None
    window_days = min(30, (latest-earliest).days+1) if latest else 0
    cutoff = latest - timedelta(days=window_days-1) if latest else None
    lookup = defaultdict(list)
    for p in positions.values(): lookup[(p['material'], p['bin'])].append(p)
    weekly, turnaround, frequent = Counter(), [], Counter()
    matched_events = ambiguous_events = unmatched_events = 0
    window_events = 0
    for event in transfers:
        monday = event['confirmed'].date() - timedelta(days=event['confirmed'].weekday())
        weekly[monday.isoformat()] += 1
        turnaround.append(event['hours'])
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
    inventory = sorted(positions.values(), key=lambda p: (p['stock'] is None,
                       0 if p['stock'] is not None and p['stock'] <= 0 else 1,
                       p['stock_to_target_days'] if p['stock_to_target_days'] is not None else math.inf, p['material'], p['bin']))

    production_rows = records.get('HISTORICAL_PROD_DATA', [])
    production_dates, monthly = [], defaultdict(lambda: dict(events=0, passed=0, held=0, orders=set()))
    statuses, stations = Counter(), defaultdict(lambda: dict(events=0, held=0, durations=[]))
    orders = set()
    for row in production_rows:
        status = label(row.get('statusstringDescription')) or 'Unknown'
        statuses[status] += 1
        order = identifier(row.get('productionorderOrdNr'))
        if order: orders.add(order)
        at = local_date(row.get('TransactionTime'), date_system)
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

    schedule_rows = records.get('PLANNED_PROD_SCHEDULE', [])
    schedule_dates, schedule_months, scheduled_keys = [], Counter(), set()
    for row in schedule_rows:
        dt = local_date(row.get('_start_at_local'), date_system)
        if not dt: continue
        schedule_dates.append(dt); schedule_months[dt.strftime('%Y-%m')] += 1
        key = (identifier(row.get('Sales Order')), identifier(row.get('Line Item')), dt.date().isoformat())
        if all(key): scheduled_keys.add(key)
    return dict(version=ANALYSIS_VERSION, forecast_validated=False, snapshot_as_of=None,
                inventory=inventory, inventory_summary=dict(positions=len(inventory), materials=len({p['material'] for p in inventory}),
                    source_rows=len(bins), duplicate_rows=duplicate_stock_rows, missing_key_rows=missing_stock_keys,
                    empty_positions=sum(p['stock'] == 0 for p in inventory), invalid_positions=sum(p['stock'] is None or p['stock'] < 0 for p in inventory),
                    proxy_eligible_positions=sum(p['scenario_eligible'] for p in inventory)),
                transfers=dict(source_rows=len(records.get('HISTORICAL_SM_REPLENISHMENTS', [])), valid_events=len(transfers),
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
                    monthly=[dict(period=k,events=v['events'],passed=v['passed'],held=v['held'],orders=len(v['orders'])) for k,v in sorted(monthly.items())]),
                schedule=dict(source_rows=len(schedule_rows), dated_rows=len(schedule_dates), unique_lines_by_date=len(scheduled_keys),
                    date_range=range_of(schedule_dates), monthly=[dict(period=k,count=v) for k,v in sorted(schedule_months.items())]),
                assumptions=[
                    'The bin snapshot has no verified as-of timestamp. Empty means zero available stock in that snapshot, not a live factory stockout.',
                    'Transfer confirmation time is a recorded system interval, not validated physical warehouse lead time; zero intervals may reflect automatic confirmation.',
                    'Dest.target quantity is a transfer target, not actual received quantity or consumption. Stock divided by average confirmed target quantity is only a descriptive coverage proxy.',
                    'Transfers do not declare a unit. The proxy assumes destination quantities use the matched bin count unit (PC or EA); confirm this before operational use.',
                    'Production rows are station events, not finished units. Planned rows are sales-order lines, not units or material demand. No BOM-based mapping is assumed.',
                    'The rate averages over calendar days ending at the latest valid transfer, including quiet days and potentially partial endpoint days. No shift calendar is available.',
                ])


def scenario(analysis, position_id, usage_multiplier, minimum_stock, lead_hours):
    multiplier = number(usage_multiplier, 'Usage multiplier', 0.1, 5)
    minimum = number(minimum_stock, 'Assumed minimum', 0, 1e6)
    lead = number(lead_hours, 'Assumed warehouse lead', 0.1, 168)
    row = next((r for r in analysis['inventory'] if r['id'] == position_id), None)
    if row is None or not row['scenario_eligible'] or not row['target_quantity_per_day']:
        raise ValueError('Choose a bin with valid count-unit stock and unambiguous positive transfer activity.')
    rate = row['target_quantity_per_day'] * multiplier
    hours = max(0, row['stock'] - minimum) / rate * 24
    return dict(kind='assumption_based', position_id=position_id, material=row['material'], bin=row['bin'], unit=row['unit'],
                snapshot_stock=row['stock'], target_quantity_per_day=row['target_quantity_per_day'],
                assumed_consumption_per_day=rate, usage_multiplier=multiplier, minimum_stock=minimum,
                lead_hours=lead, assumed_hours_to_minimum=hours, within_assumed_lead=hours <= lead, crossing_at=None,
                assumptions=['Assumes confirmed transfer target quantities were actually received, and outbound use equals their average rate multiplied by the chosen factor.',
                             'Assumes the undated snapshot is the starting balance, transfer quantities use the bin unit, and no new deliveries arrive.',
                             'No actual depletion date, stockout probability, or production accuracy is established.'])
