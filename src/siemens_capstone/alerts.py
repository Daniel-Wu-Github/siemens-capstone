"""Proactive replenishment early warning and notification engine.

Evaluates predicted days-to-minimum against warehouse lead times and safety buffers
to generate prioritized, actionable replenishment recommendations.
"""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import uuid

from .schemas import BinMaster, clean_num
from .ml_engine import DepletionForecast


@dataclass(frozen=True)
class ReplenishmentAlert:
    id: str
    part_id: str
    bin_location: str
    current_stock: float
    min_stock: float
    max_capacity: float
    headroom_units: float
    p10_days_to_min: Optional[float]
    p50_days_to_min: Optional[float]
    lead_time_days: float
    warning_buffer_days: float
    urgency: str  # 'CRITICAL', 'WATCH', 'HEALTHY'
    priority_level: str  # 'P1 - Expedited', 'P2 - Proactive Replenish', 'P3 - Healthy'
    recommended_order_qty: float
    breach_prob_3d: float
    created_at: str
    estimated_crossing_date: Optional[str]
    explanation: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EarlyWarningNotificationEngine:
    """Evaluates parts and generates prioritized replenishment alerts."""

    def __init__(self, default_warning_buffer_days: float = 3.0, default_lead_time_days: float = 2.0):
        self.default_warning_buffer_days = default_warning_buffer_days
        self.default_lead_time_days = default_lead_time_days

    def evaluate_forecast(
        self,
        forecast: DepletionForecast,
        bin_master: BinMaster,
        lead_time_stats: Optional[Dict[str, float]] = None,
        warning_buffer_days: Optional[float] = None
    ) -> ReplenishmentAlert:
        """Evaluate a single part's forecast and return an actionable alert recommendation."""
        buffer_days = warning_buffer_days if warning_buffer_days is not None else self.default_warning_buffer_days
        lt_stats = lead_time_stats or {}
        lead_days = float(lt_stats.get('lead_time_p95_days', self.default_lead_time_days))

        stock = forecast.current_stock
        min_val = bin_master.min_stock
        max_cap = bin_master.max_capacity
        headroom = forecast.headroom_units
        p10 = forecast.p10_days_to_min
        p50 = forecast.p50_days_to_min

        # Recommended replenishment order quantity (reorder up to max capacity)
        order_qty = max(0.0, max_cap - stock)

        # Decision Logic:
        if stock <= min_val:
            urgency = 'CRITICAL'
            priority = 'P1 - Immediate Stockout Risk'
            explanation = (f"Stock ({stock:.0f} PC) has breached minimum threshold ({min_val:.0f} PC). "
                           f"Immediate warehouse dispatch required.")
        elif p10 is not None and p10 <= lead_days:
            urgency = 'CRITICAL'
            priority = 'P1 - Expedited Replenishment'
            explanation = (f"Worst-case minimum breach projected in {p10:.1f} days, which is within "
                           f"warehouse lead time ({lead_days:.1f} days). Expedited delivery needed.")
        elif p10 is not None and p10 <= (lead_days + buffer_days):
            urgency = 'WATCH'
            priority = 'P2 - Proactive Replenishment'
            explanation = (f"Minimum breach projected in {p10:.1f} days (expected P50: {p50:.1f} days), "
                           f"entering the {buffer_days:.1f}-day early warning window before lead time ({lead_days:.1f} days).")
        else:
            urgency = 'HEALTHY'
            priority = 'P3 - Healthy Stock'
            if p50 is not None:
                explanation = f"Stock is stable. Minimum breach projected in {p50:.1f} days, beyond warning window."
            else:
                explanation = "Stock is safe with no finite minimum breach projected over the 14-day schedule."

        return ReplenishmentAlert(
            id=uuid.uuid4().hex[:12],
            part_id=forecast.part_id,
            bin_location=bin_master.bin_location,
            current_stock=stock,
            min_stock=min_val,
            max_capacity=max_cap,
            headroom_units=headroom,
            p10_days_to_min=p10,
            p50_days_to_min=p50,
            lead_time_days=lead_days,
            warning_buffer_days=buffer_days,
            urgency=urgency,
            priority_level=priority,
            recommended_order_qty=round(order_qty, 2),
            breach_prob_3d=forecast.breach_prob_3d,
            created_at=datetime.now(timezone.utc).isoformat(),
            estimated_crossing_date=forecast.estimated_crossing_date,
            explanation=explanation,
        )

    def generate_alerts(
        self,
        forecasts: List[DepletionForecast],
        bin_masters: Dict[str, BinMaster],
        lead_times: Optional[Dict[str, Dict[str, float]]] = None,
        warning_buffer_days: Optional[float] = None
    ) -> List[ReplenishmentAlert]:
        """Generate, categorize, and prioritize replenishment alerts for all monitored parts."""
        lt_dict = lead_times or {}
        alerts = []

        for f in forecasts:
            master = bin_masters.get(f.part_id)
            if not master:
                continue
            part_lt = lt_dict.get(f.part_id, lt_dict.get('__default__', {}))
            alert = self.evaluate_forecast(f, master, part_lt, warning_buffer_days)
            alerts.append(alert)

        # Prioritize: CRITICAL first, then WATCH, then HEALTHY; within urgency sort by earliest p10
        urgency_rank = {'CRITICAL': 0, 'WATCH': 1, 'HEALTHY': 2}
        alerts.sort(key=lambda a: (
            urgency_rank.get(a.urgency, 9),
            a.p10_days_to_min if a.p10_days_to_min is not None else 999.0,
            a.part_id
        ))

        return alerts
