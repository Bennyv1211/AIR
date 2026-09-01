from __future__ import annotations

from datetime import date, timedelta
from math import ceil

from app.models import BusinessAssumptions, Recommendation, ReplenishmentRecord
from app.services.questionnaire import classify_item

REVIEW_PERIOD_DAYS = 7
WEEKDAYS = {
    "Monday": 0,
    "Tuesday": 1,
    "Wednesday": 2,
    "Thursday": 3,
    "Friday": 4,
    "Saturday": 5,
    "Sunday": 6,
}


def build_recommendations(
    records: list[ReplenishmentRecord],
    usage_overrides: dict[str, float] | None = None,
    assumptions: BusinessAssumptions | None = None,
) -> list[Recommendation]:
    recommendations = [
        build_recommendation(record, usage_overrides, assumptions) for record in records
    ]
    return sorted(
        recommendations,
        key=lambda item: (not item.needs_reorder, _priority_rank(item.priority), -item.stock_gap),
    )


def build_recommendation(
    record: ReplenishmentRecord,
    usage_overrides: dict[str, float] | None = None,
    assumptions: BusinessAssumptions | None = None,
) -> Recommendation:
    today = date.today()
    refined_daily_demand, demand_source = _refined_daily_demand(record, usage_overrides or {})
    effective_stock = record.current_stock + record.incoming_stock
    effective_lead_time = _effective_lead_time(today, record.lead_time_days, assumptions)
    order_cycle_days = _order_cycle_days(assumptions)
    spoilage_limit_days = _spoilage_limit_days(record, assumptions)
    zero_demand = refined_daily_demand <= 0
    # The reorder point is the lean trigger: demand while a new order is arriving,
    # plus the explicitly supplied safety stock. The target can cover the next cycle.
    reorder_point = ceil(refined_daily_demand * effective_lead_time) + record.safety_stock
    target_coverage_days = max(effective_lead_time, order_cycle_days)
    target_stock = ceil(refined_daily_demand * target_coverage_days) + record.safety_stock
    if spoilage_limit_days is not None:
        spoilage_target = ceil(refined_daily_demand * spoilage_limit_days) + record.safety_stock
        target_stock = min(target_stock, max(reorder_point, spoilage_target))
    stock_gap = max(target_stock - effective_stock, 0)
    needs_reorder = effective_stock < reorder_point if not zero_demand else effective_stock < record.safety_stock
    recommended_order_qty = max(record.min_order_qty, stock_gap) if needs_reorder else 0
    days_until_stockout = _days_until_stockout(effective_stock, refined_daily_demand)
    projected_stockout_date = (
        today + timedelta(days=days_until_stockout) if days_until_stockout is not None else None
    )
    priority = _priority(effective_stock, reorder_point)

    return Recommendation(
        sku=record.sku,
        name=record.name,
        reorder_point=reorder_point,
        target_stock=target_stock,
        current_stock=record.current_stock,
        stock_gap=stock_gap,
        recommended_order_qty=recommended_order_qty,
        needs_reorder=needs_reorder,
        priority=priority,
        days_until_stockout=days_until_stockout,
        projected_stockout_date=projected_stockout_date,
        demand_source=demand_source,
        explanation=_build_explanation(
            record=record,
            assumptions=assumptions,
            effective_stock=effective_stock,
            reorder_point=reorder_point,
            recommended_order_qty=recommended_order_qty,
            needs_reorder=needs_reorder,
            priority=priority,
            days_until_stockout=days_until_stockout,
            refined_daily_demand=refined_daily_demand,
            demand_source=demand_source,
            effective_lead_time=effective_lead_time,
            order_cycle_days=order_cycle_days,
            target_coverage_days=target_coverage_days,
            spoilage_limit_days=spoilage_limit_days,
        ),
    )


def _priority(current_stock: int, reorder_point: int) -> str:
    if current_stock == 0:
        return "critical"
    if current_stock <= max(1, reorder_point // 2):
        return "high"
    if current_stock <= reorder_point:
        return "medium"
    return "low"


def _priority_rank(priority: str) -> int:
    ranking = {
        "critical": 0,
        "high": 1,
        "medium": 2,
        "low": 3,
    }
    return ranking[priority]


def _days_until_stockout(current_stock: int, daily_demand: float) -> int | None:
    if daily_demand <= 0:
        return None
    if current_stock == 0:
        return 0
    return ceil(current_stock / daily_demand)


def _refined_daily_demand(
    record: ReplenishmentRecord,
    usage_overrides: dict[str, float],
) -> tuple[float, str]:
    usage_daily_demand = usage_overrides.get(record.sku)
    if usage_daily_demand is None:
        return record.daily_demand, "snapshot"
    return usage_daily_demand, "daily-usage history"


def _build_explanation(
    record: ReplenishmentRecord,
    assumptions: BusinessAssumptions | None,
    effective_stock: int,
    reorder_point: int,
    recommended_order_qty: int,
    needs_reorder: bool,
    priority: str,
    days_until_stockout: int | None,
    refined_daily_demand: float,
    demand_source: str,
    effective_lead_time: int,
    order_cycle_days: int,
    target_coverage_days: int,
    spoilage_limit_days: int | None,
) -> str:
    incoming_note = (
        f" AIR included {record.incoming_stock} unit(s) already on order in the analysis."
        if record.incoming_stock
        else ""
    )
    demand_note = (
        f" AIR used a refined demand rate of {refined_daily_demand:.2f} units/day from {demand_source}."
    )
    lead_time_note = f" AIR used an effective lead time of {effective_lead_time} day(s)."
    review_note = f" AIR used a delivery cycle of about {order_cycle_days} day(s) between replenishment opportunities."
    reorder_note = (
        f" AIR set the reorder point from {effective_lead_time} day(s) of demand plus safety stock, "
        "so it does not include an unnecessary extra ordering cycle."
    )
    sizing_note = (
        f" AIR sized the order toward about {target_coverage_days} day(s) of coverage for the current "
        "delivery cycle only, then reassesses next time."
    )
    zero_demand_note = (
        " AIR detected no active demand for this item in the current dataset."
        if refined_daily_demand <= 0
        else ""
    )
    notes_note = (
        f" AIR also considered your note: {record_note}."
        if (record_note := record_context_note(record, assumptions))
        else ""
    )
    spoilage_note = (
        f" AIR capped inventory planning to about {spoilage_limit_days} day(s) because this item appears perishable."
        if spoilage_limit_days is not None
        else ""
    )
    if needs_reorder:
        stockout_window = (
            f"Stock may run out in about {days_until_stockout} day(s)."
            if days_until_stockout is not None
            else "Stockout timing is not applicable right now."
        )
        return (
            f"Reorder now. {record.name} is at priority {priority} because current stock "
            f"plus incoming inventory ({effective_stock}) is below the reorder point "
            f"({reorder_point}). {stockout_window} Recommended order quantity: "
            f"{recommended_order_qty}.{incoming_note} {demand_note}{lead_time_note} {review_note} {reorder_note} {sizing_note} "
            f"{spoilage_note}{zero_demand_note}{notes_note}"
        )

    return (
        f"No immediate reorder needed. {record.name} is above the reorder point "
        f"({reorder_point}) with an effective stock position of {effective_stock}.{incoming_note} "
        f"{demand_note}{lead_time_note} {review_note} {reorder_note} {sizing_note} {spoilage_note}{zero_demand_note}{notes_note}"
    )


def _effective_lead_time(
    today: date,
    base_lead_time: int,
    assumptions: BusinessAssumptions | None,
) -> int:
    if assumptions is None or not assumptions.arrival_days:
        return base_lead_time
    arrival_offsets = [
        (WEEKDAYS[day] - today.weekday()) % 7
        for day in assumptions.arrival_days
        if day in WEEKDAYS
    ]
    if not arrival_offsets:
        return base_lead_time
    arrival_wait = min(offset for offset in arrival_offsets if offset > 0) if any(
        offset > 0 for offset in arrival_offsets
    ) else 7
    return base_lead_time + arrival_wait


def _spoilage_limit_days(
    record: ReplenishmentRecord,
    assumptions: BusinessAssumptions | None,
) -> int | None:
    item_type = classify_item(record)
    if item_type == "herb":
        if assumptions and assumptions.herb_spoilage_days is not None:
            return assumptions.herb_spoilage_days
        return 3
    if item_type == "refrigerated-produce":
        if assumptions and assumptions.produce_spoilage_days is not None:
            return assumptions.produce_spoilage_days
        return 5
    if item_type == "produce":
        if assumptions and assumptions.produce_spoilage_days is not None:
            return assumptions.produce_spoilage_days
        return 7
    if item_type == "dry":
        return None
    if assumptions:
        return assumptions.default_spoilage_days
    return None


def _order_cycle_days(assumptions: BusinessAssumptions | None) -> int:
    if assumptions and assumptions.arrival_days:
        arrival_indexes = sorted({WEEKDAYS[day] for day in assumptions.arrival_days if day in WEEKDAYS})
        if len(arrival_indexes) == 1:
            return 7
        if arrival_indexes:
            gaps = [
                (arrival_indexes[(index + 1) % len(arrival_indexes)] - day) % 7 or 7
                for index, day in enumerate(arrival_indexes)
            ]
            return max(1, max(gaps))
    if assumptions and assumptions.shipping_days_per_week is not None:
        return max(1, ceil(7 / max(assumptions.shipping_days_per_week, 1)))
    return REVIEW_PERIOD_DAYS


def record_context_note(
    record: ReplenishmentRecord,
    assumptions: BusinessAssumptions | None,
) -> str:
    if assumptions is None or not assumptions.additional_notes.strip():
        return ""
    return assumptions.additional_notes.strip()
