"""Data contracts and validation for Bill of Materials (BOM), routings, and bin masters.

Provides schemas and sanitization to connect production orders with supermarket
parts and inventory minimums.
"""
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import math
import re


def clean_str(val: Any) -> str:
    if val is None:
        return ''
    s = str(val).strip()
    # Normalize trailing float representations like 21010.0 -> 21010
    if re.fullmatch(r'\d+\.0+', s):
        return s.split('.')[0]
    return s


def clean_num(val: Any, name: str, minimum: float = 0.0, maximum: float = 1e9) -> float:
    if isinstance(val, bool):
        raise ValueError(f"{name} must be a number, not a boolean.")
    try:
        n = float(val)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be numeric, got {val!r}") from None
    if not math.isfinite(n):
        raise ValueError(f"{name} must be finite.")
    if n < minimum or n > maximum:
        raise ValueError(f"{name} ({n}) must be between {minimum} and {maximum}.")
    return n


@dataclass(frozen=True)
class BOMItem:
    item_id: str
    part_id: str
    description: str
    qty_per_item: float
    mat_type: str
    subassembly: str


@dataclass(frozen=True)
class RoutingStep:
    item_id: str
    routing_code: str
    station_name: str
    sequence_order: int


@dataclass(frozen=True)
class BinMaster:
    part_id: str
    description: str
    part_type: str
    bin_location: str
    min_stock: float
    max_capacity: float

    @property
    def safety_headroom_capacity(self) -> float:
        return max(0.0, self.max_capacity - self.min_stock)


def parse_bom_records(rows: List[Dict[str, Any]]) -> List[BOMItem]:
    """Parse and validate BOM records mapping Line Items to Parts."""
    items = []
    for idx, r in enumerate(rows):
        item_id = clean_str(r.get('Item') or r.get('item_id') or r.get('productionorderSalesOrderLineItem'))
        part_id = clean_str(r.get('Part') or r.get('part_id') or r.get('Material'))
        desc = clean_str(r.get('Description') or r.get('description') or '')
        qty_raw = r.get('Qty') if 'Qty' in r else r.get('qty_per_item', 1.0)
        mat_type = clean_str(r.get('Mat Type') or r.get('mat_type') or 'M')
        subassembly = clean_str(r.get('SubAssembly') or r.get('subassembly') or '')

        if not item_id:
            raise ValueError(f"BOM row {idx}: Missing Item/LineItem identifier.")
        if not part_id:
            raise ValueError(f"BOM row {idx}: Missing Part identifier.")
        qty = clean_num(qty_raw, f"BOM row {idx} Qty", minimum=0.001)

        items.append(BOMItem(
            item_id=item_id,
            part_id=part_id,
            description=desc,
            qty_per_item=qty,
            mat_type=mat_type,
            subassembly=subassembly,
        ))
    return items


def parse_routing_records(rows: List[Dict[str, Any]]) -> List[RoutingStep]:
    """Parse and validate Routing records mapping Line Items to Station/Process sequences."""
    steps = []
    item_counts: Dict[str, int] = {}
    for idx, r in enumerate(rows):
        item_id = clean_str(r.get('Item') or r.get('item_id'))
        routing_code = clean_str(r.get('Routing') or r.get('routing_code'))
        if not item_id:
            raise ValueError(f"Routing row {idx}: Missing Item identifier.")
        if not routing_code:
            raise ValueError(f"Routing row {idx}: Missing Routing code.")

        seq = item_counts.get(item_id, 0) + 1
        item_counts[item_id] = seq

        # Extract station token if structured like CCO07_MSB01-LH-S1-P6 -> S1-P6
        parts = routing_code.split('-')
        station = parts[-1] if len(parts) > 1 else routing_code

        steps.append(RoutingStep(
            item_id=item_id,
            routing_code=routing_code,
            station_name=station,
            sequence_order=seq,
        ))
    return steps


def parse_bin_master_records(rows: List[Dict[str, Any]]) -> Dict[str, BinMaster]:
    """Parse and validate Supermarket Bin Master rows with Min/Max thresholds."""
    masters: Dict[str, BinMaster] = {}
    for idx, r in enumerate(rows):
        part_id = clean_str(r.get('Part') or r.get('part_id') or r.get('Material'))
        if not part_id:
            raise ValueError(f"Bin Master row {idx}: Missing Part identifier.")
        desc = clean_str(r.get('Description') or r.get('description') or '')
        ptype = clean_str(r.get('Type') or r.get('type') or '')
        loc = clean_str(r.get('Location') or r.get('location') or r.get('Storage Bin') or '')
        min_val = clean_num(r.get('Min') if 'Min' in r else r.get('min_stock', 0), f"Bin Master row {idx} Min", minimum=0.0)
        max_val = clean_num(r.get('Max') if 'Max' in r else r.get('max_capacity', min_val + 10), f"Bin Master row {idx} Max", minimum=min_val)

        # In case of duplicate bin mappings for same part, keep highest threshold
        if part_id in masters:
            prev = masters[part_id]
            min_val = max(min_val, prev.min_stock)
            max_val = max(max_val, prev.max_capacity)

        masters[part_id] = BinMaster(
            part_id=part_id,
            description=desc,
            part_type=ptype,
            bin_location=loc,
            min_stock=min_val,
            max_capacity=max_val,
        )
    return masters


def generate_synthetic_manufacturing_fixtures() -> Dict[str, Any]:
    """Deterministic synthetic fixture generator for testing pipelines offline without real data."""
    bom_raw = [
        {'Item': '21010', 'Part': 'PART-INSUL-01', 'Description': 'Standoff Insulator', 'Qty': 4, 'Mat Type': 'M', 'SubAssembly': 'SUB-A'},
        {'Item': '21010', 'Part': 'PART-BRACKET-01', 'Description': 'Mounting Bracket', 'Qty': 2, 'Mat Type': 'M', 'SubAssembly': 'SUB-A'},
        {'Item': '21010', 'Part': 'PART-BUSBAR-CU', 'Description': 'Copper Ground Bus', 'Qty': 1, 'Mat Type': 'M', 'SubAssembly': 'SUB-B'},
        {'Item': '21020', 'Part': 'PART-INSUL-01', 'Description': 'Standoff Insulator', 'Qty': 10, 'Mat Type': 'M', 'SubAssembly': 'SUB-C'},
        {'Item': '21020', 'Part': 'PART-BKR-3WA', 'Description': '3WA Breaker Assembly', 'Qty': 1, 'Mat Type': 'M', 'SubAssembly': 'SUB-C'},
        {'Item': '21020', 'Part': 'PART-SPACER-01', 'Description': 'Copper Spacer', 'Qty': 8, 'Mat Type': 'M', 'SubAssembly': 'SUB-C'},
        {'Item': '22010', 'Part': 'PART-INSUL-01', 'Description': 'Standoff Insulator', 'Qty': 4, 'Mat Type': 'M', 'SubAssembly': 'SUB-A'},
        {'Item': '22010', 'Part': 'PART-BUSBAR-CU', 'Description': 'Copper Ground Bus', 'Qty': 2, 'Mat Type': 'M', 'SubAssembly': 'SUB-B'},
        {'Item': '22020', 'Part': 'PART-SPACER-01', 'Description': 'Copper Spacer', 'Qty': 12, 'Mat Type': 'M', 'SubAssembly': 'SUB-D'},
        {'Item': '22020', 'Part': 'PART-BKR-3WA', 'Description': '3WA Breaker Assembly', 'Qty': 1, 'Mat Type': 'M', 'SubAssembly': 'SUB-D'},
    ]

    routing_raw = [
        {'Item': '21010', 'Routing': 'CCO07_MSB01-S1-P6'},
        {'Item': '21010', 'Routing': 'CCO07_MSB01-S1-TEST01'},
        {'Item': '21020', 'Routing': 'CCO07_MSB01-S2-M3'},
        {'Item': '21020', 'Routing': 'CCO07_MSB01-S2-M4'},
        {'Item': '22010', 'Routing': 'CCO07_MSB01-S1-P6'},
        {'Item': '22020', 'Routing': 'CCO07_MSB01-S2-M3'},
    ]

    bins_raw = [
        {'Part': 'PART-INSUL-01', 'Description': 'Standoff Insulator', 'Type': '401', 'Location': 'BIN-A-01', 'Min': 10, 'Max': 60},
        {'Part': 'PART-BRACKET-01', 'Description': 'Mounting Bracket', 'Type': '402', 'Location': 'BIN-A-02', 'Min': 5, 'Max': 30},
        {'Part': 'PART-BUSBAR-CU', 'Description': 'Copper Ground Bus', 'Type': '403', 'Location': 'BIN-B-01', 'Min': 8, 'Max': 40},
        {'Part': 'PART-BKR-3WA', 'Description': '3WA Breaker Assembly', 'Type': '404', 'Location': 'BIN-C-01', 'Min': 2, 'Max': 10},
        {'Part': 'PART-SPACER-01', 'Description': 'Copper Spacer', 'Type': '405', 'Location': 'BIN-D-01', 'Min': 20, 'Max': 100},
    ]

    return {
        'bom': parse_bom_records(bom_raw),
        'routing': parse_routing_records(routing_raw),
        'bin_masters': parse_bin_master_records(bins_raw),
    }
