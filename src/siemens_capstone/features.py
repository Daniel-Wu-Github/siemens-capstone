"""Feature engineering pipeline for predicting material depletion and minimum crossings.

Computes planned BOM demand projections, inventory headroom ratios,
part criticality metrics, and replenishment latency statistics.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, date, timedelta, timezone
from statistics import mean, median
from typing import List, Dict, Any, Optional, Tuple
import math

from .schemas import BOMItem, RoutingStep, BinMaster, clean_str, clean_num


def parse_iso_date(dt_val: Any) -> date:
    """Normalize a date, datetime, or ISO string to a date object."""
    if isinstance(dt_val, datetime):
        return dt_val.date()
    if isinstance(dt_val, date):
        return dt_val
    s = str(dt_val).strip().split('T')[0].split(' ')[0]
    return date.fromisoformat(s)


@dataclass(frozen=True)
class PartDemandSchedule:
    part_id: str
    as_of_date: date
    demand_1d: float
    demand_3d: float
    demand_7d: float
    demand_14d: float
    contributing_orders: int
    daily_schedule: Dict[str, float]


def aggregate_scheduled_bom_demand(
    schedule_rows: List[Dict[str, Any]],
    bom_items: List[BOMItem],
    as_of: Any
) -> Dict[str, PartDemandSchedule]:
    """Join planned production schedule with Bill of Materials to compute part demand windows."""
    as_of_d = parse_iso_date(as_of)

    # Index BOM by item_id -> list of (part_id, qty)
    bom_by_item: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
    for b in bom_items:
        bom_by_item[b.item_id].append((b.part_id, b.qty_per_item))

    # Aggregate daily demand per part
    part_daily: Dict[str, Dict[date, float]] = defaultdict(lambda: defaultdict(float))
    part_order_count: Dict[str, int] = defaultdict(int)

    for row in schedule_rows:
        item_id = clean_str(row.get('Line Item') or row.get('Item') or row.get('item_id'))
        raw_date = row.get('Basic Start Date') or row.get('start_date') or row.get('Date')
        if not item_id or not raw_date:
            continue
        try:
            order_date = parse_iso_date(raw_date)
        except (ValueError, TypeError):
            continue

        # Look up parts required by this line item
        components = bom_by_item.get(item_id, [])
        for part_id, qty in components:
            part_daily[part_id][order_date] += qty
            part_order_count[part_id] += 1

    # Compute rolling horizons: 1d, 3d, 7d, 14d from as_of_d
    results: Dict[str, PartDemandSchedule] = {}
    all_parts = set(part_daily.keys()).union(b.part_id for b in bom_items)

    for part_id in all_parts:
        daily = part_daily.get(part_id, {})
        d1 = sum(qty for dt, qty in daily.items() if as_of_d <= dt < as_of_d + timedelta(days=1))
        d3 = sum(qty for dt, qty in daily.items() if as_of_d <= dt < as_of_d + timedelta(days=3))
        d7 = sum(qty for dt, qty in daily.items() if as_of_d <= dt < as_of_d + timedelta(days=7))
        d14 = sum(qty for dt, qty in daily.items() if as_of_d <= dt < as_of_d + timedelta(days=14))

        results[part_id] = PartDemandSchedule(
            part_id=part_id,
            as_of_date=as_of_d,
            demand_1d=round(d1, 2),
            demand_3d=round(d3, 2),
            demand_7d=round(d7, 2),
            demand_14d=round(d14, 2),
            contributing_orders=part_order_count.get(part_id, 0),
            daily_schedule={dt.isoformat(): round(qty, 2) for dt, qty in sorted(daily.items()) if dt >= as_of_d},
        )

    return results


def compute_inventory_headroom(
    current_stock: float,
    min_stock: float,
    max_capacity: float
) -> Dict[str, float]:
    """Calculate buffer headroom, fill ratios, and depletion vulnerability."""
    stock = clean_num(current_stock, "Current stock", minimum=0.0)
    min_val = clean_num(min_stock, "Min stock", minimum=0.0)
    max_val = clean_num(max_capacity, "Max capacity", minimum=min_val)

    headroom = stock - min_val
    fill_ratio = (stock / max_val) if max_val > 0 else 0.0
    buffer_ratio = (headroom / (max_val - min_val)) if (max_val > min_val) else 0.0

    return {
        'current_stock': stock,
        'min_stock': min_val,
        'max_capacity': max_val,
        'headroom': headroom,
        'is_below_min': 1.0 if stock <= min_val else 0.0,
        'fill_ratio': round(fill_ratio, 4),
        'buffer_ratio': round(max(-1.0, min(1.0, buffer_ratio)), 4),
    }


def compute_part_criticality(bom_items: List[BOMItem]) -> Dict[str, Dict[str, Any]]:
    """Determine multi-use risk: how many line items and subassemblies share this part."""
    part_items: Dict[str, set] = defaultdict(set)
    part_subs: Dict[str, set] = defaultdict(set)
    part_max_qty: Dict[str, float] = defaultdict(float)

    for b in bom_items:
        part_items[b.part_id].add(b.item_id)
        if b.subassembly:
            part_subs[b.part_id].add(b.subassembly)
        part_max_qty[b.part_id] = max(part_max_qty[b.part_id], b.qty_per_item)

    metrics = {}
    for part_id in part_items:
        sharing_count = len(part_items[part_id])
        metrics[part_id] = {
            'distinct_line_items': sharing_count,
            'distinct_subassemblies': len(part_subs[part_id]),
            'max_qty_single_order': part_max_qty[part_id],
            'is_shared_across_multiple_lines': 1.0 if sharing_count > 1 else 0.0,
        }
    return metrics


def extract_replenishment_latency(
    replenishment_rows: List[Dict[str, Any]]
) -> Dict[str, Dict[str, float]]:
    """Compute warehouse-to-supermarket delivery lead time percentiles (p50, p95)."""
    part_durations: Dict[str, List[float]] = defaultdict(list)
    all_durations: List[float] = []

    for r in replenishment_rows:
        part_id = clean_str(r.get('Material') or r.get('part_id'))
        # Try pre-computed local times or compute difference
        start = r.get('_created_at_local') or r.get('Creation Date')
        end = r.get('_confirmed_at_local') or r.get('Confirmation Date')
        if not part_id or not start or not end:
            continue

        try:
            dt_start = datetime.fromisoformat(str(start).replace('Z', '+00:00')) if isinstance(start, str) else datetime.combine(start, datetime.min.time())
            dt_end = datetime.fromisoformat(str(end).replace('Z', '+00:00')) if isinstance(end, str) else datetime.combine(end, datetime.min.time())
            hours = (dt_end - dt_start).total_seconds() / 3600.0
            if 0.1 <= hours <= 720.0:
                part_durations[part_id].append(hours)
                all_durations.append(hours)
        except (ValueError, TypeError):
            continue

    default_p50 = median(all_durations) if all_durations else 24.0
    sorted_all = sorted(all_durations) if all_durations else [48.0]
    default_p95 = sorted_all[int(0.95 * len(sorted_all))] if all_durations else 48.0

    stats: Dict[str, Dict[str, float]] = {}
    for part_id, durs in part_durations.items():
        if len(durs) >= 3:
            s = sorted(durs)
            p50 = median(s)
            p95 = s[int(0.95 * len(s))]
            stats[part_id] = {
                'lead_time_p50_hours': round(p50, 2),
                'lead_time_p95_hours': round(p95, 2),
                'lead_time_p95_days': round(p95 / 24.0, 2),
                'samples': len(durs),
            }
        else:
            stats[part_id] = {
                'lead_time_p50_hours': round(default_p50, 2),
                'lead_time_p95_hours': round(default_p95, 2),
                'lead_time_p95_days': round(default_p95 / 24.0, 2),
                'samples': len(durs),
            }

    # Store a global default entry
    stats['__default__'] = {
        'lead_time_p50_hours': round(default_p50, 2),
        'lead_time_p95_hours': round(default_p95, 2),
        'lead_time_p95_days': round(default_p95 / 24.0, 2),
        'samples': len(all_durations),
    }
    return stats


def build_feature_vector(
    part_id: str,
    current_stock: float,
    bin_master: BinMaster,
    demand_schedule: PartDemandSchedule,
    criticality: Optional[Dict[str, Any]] = None,
    lead_time_stats: Optional[Dict[str, float]] = None
) -> Dict[str, float]:
    """Construct a full tabular feature vector for ML model inference."""
    headroom_info = compute_inventory_headroom(current_stock, bin_master.min_stock, bin_master.max_capacity)
    hr = headroom_info['headroom']

    d1 = demand_schedule.demand_1d
    d3 = demand_schedule.demand_3d
    d7 = demand_schedule.demand_7d
    d14 = demand_schedule.demand_14d

    crit = criticality or {'distinct_line_items': 1, 'distinct_subassemblies': 1, 'is_shared_across_multiple_lines': 0.0}
    lt = lead_time_stats or {'lead_time_p50_hours': 24.0, 'lead_time_p95_hours': 48.0, 'lead_time_p95_days': 2.0}

    # Ratio of headroom to planned demand (coverage days)
    cov_3d = hr / (d3 / 3.0) if d3 > 0.0 else 999.0
    cov_7d = hr / (d7 / 7.0) if d7 > 0.0 else 999.0

    return {
        'current_stock': current_stock,
        'min_stock': bin_master.min_stock,
        'max_capacity': bin_master.max_capacity,
        'headroom_units': hr,
        'fill_ratio': headroom_info['fill_ratio'],
        'buffer_ratio': headroom_info['buffer_ratio'],
        'demand_1d': d1,
        'demand_3d': d3,
        'demand_7d': d7,
        'demand_14d': d14,
        'coverage_days_3d': round(min(cov_3d, 999.0), 2),
        'coverage_days_7d': round(min(cov_7d, 999.0), 2),
        'distinct_line_items': float(crit.get('distinct_line_items', 1)),
        'is_shared_part': float(crit.get('is_shared_across_multiple_lines', 0.0)),
        'lead_time_p50_hours': float(lt.get('lead_time_p50_hours', 24.0)),
        'lead_time_p95_days': float(lt.get('lead_time_p95_days', 2.0)),
    }
