from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class PlanningSchedule:
    next_order_date: date
    planned_delivery_date: date
    incoming_arrival_date: date | None
    next_delivery_gap_days: int


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
    order_cycle_days = _order_cycle_days(assumptions)
    spoilage_limit_days = _spoilage_limit_days(record, assumptions)
    schedule = _planning_schedule(today, record.lead_time_days, assumptions, order_cycle_days)
    schedule_enabled = bool(assumptions and assumptions.order_days and assumptions.arrival_days)
    effective_lead_time = (
        (schedule.planned_delivery_date - schedule.next_order_date).days
        if schedule_enabled
        else _effective_lead_time(today, record.lead_time_days, assumptions)
    )
    zero_demand = refined_daily_demand <= 0
    delivery_horizon_days = (
        (schedule.planned_delivery_date - today).days
        if schedule_enabled
        else effective_lead_time
    )
    # This threshold covers only the period until the planned delivery, not stock after it.
    reorder_point = ceil(refined_daily_demand * delivery_horizon_days) + record.safety_stock
    target_coverage_days = (
        schedule.next_delivery_gap_days
        if schedule_enabled
        else max(effective_lead_time, order_cycle_days)
    )
    target_stock = ceil(refined_daily_demand * target_coverage_days) + record.safety_stock
    if spoilage_limit_days is not None:
        spoilage_target = ceil(refined_daily_demand * spoilage_limit_days) + record.safety_stock
        target_stock = min(target_stock, max(reorder_point, spoilage_target))
    stock_at_planned_delivery = (
        record.current_stock - (refined_daily_demand * delivery_horizon_days)
        if schedule_enabled
        else record.current_stock + record.incoming_stock
    )
    incoming_available_for_delivery = (
        record.incoming_stock
        if schedule.incoming_arrival_date is not None
        and schedule.incoming_arrival_date <= schedule.planned_delivery_date
        else 0
    )
    if schedule_enabled:
        stock_at_planned_delivery += incoming_available_for_delivery
    stock_gap = max(ceil(target_stock - stock_at_planned_delivery), 0)
    needs_reorder = (
        stock_gap > 0
        if schedule_enabled
        else stock_at_planned_delivery < reorder_point
    ) and not zero_demand
    recommended_order_qty = max(record.min_order_qty, stock_gap) if needs_reorder else 0
    days_until_stockout = (
        _days_until_stockout_with_incoming(
            record.current_stock,
            refined_daily_demand,
            schedule.incoming_arrival_date,
            record.incoming_stock,
            today,
        )
        if schedule_enabled
        else _days_until_stockout(record.current_stock + record.incoming_stock, refined_daily_demand)
    )
    projected_stockout_date = (
        today + timedelta(days=days_until_stockout) if days_until_stockout is not None else None
    )
    priority = _priority(max(ceil(stock_at_planned_delivery), 0), reorder_point)

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
        planned_order_date=schedule.next_order_date if schedule_enabled else None,
        planned_delivery_date=schedule.planned_delivery_date if schedule_enabled else None,
        demand_source=demand_source,
        supplier_code=record.supplier_code,
        explanation=_build_explanation(
            record=record,
            assumptions=assumptions,
            stock_at_planned_delivery=stock_at_planned_delivery,
            incoming_available_for_delivery=incoming_available_for_delivery,
            reorder_point=reorder_point,
            target_stock=target_stock,
            recommended_order_qty=recommended_order_qty,
            needs_reorder=needs_reorder,
            priority=priority,
            days_until_stockout=days_until_stockout,
            refined_daily_demand=refined_daily_demand,
            demand_source=demand_source,
            effective_lead_time=effective_lead_time,
            order_cycle_days=order_cycle_days,
            schedule=schedule,
            schedule_enabled=schedule_enabled,
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


def _days_until_stockout_with_incoming(
    current_stock: int,
    daily_demand: float,
    incoming_arrival_date: date | None,
    incoming_stock: int,
    today: date,
) -> int | None:
    stockout_days = _days_until_stockout(current_stock, daily_demand)
    if stockout_days is None or not incoming_stock or incoming_arrival_date is None:
        return stockout_days

    arrival_offset = (incoming_arrival_date - today).days
    if arrival_offset > stockout_days:
        return stockout_days
    stock_after_arrival = max(current_stock - (daily_demand * arrival_offset), 0) + incoming_stock
    return arrival_offset + _days_until_stockout(ceil(stock_after_arrival), daily_demand)


def _refined_daily_demand(
    record: ReplenishmentRecord,
    usage_overrides: dict[str, float],
) -> tuple[float, str]:
    usage_daily_demand = usage_overrides.get(record.sku)
    if usage_daily_demand is None:
        return record.daily_demand, "snapshot"
    if record.daily_demand <= 0:
        return usage_daily_demand, "daily-usage history"

    # The inventory snapshot already contains a business-maintained daily run rate.
    # Blend in movement history, but keep a short partial week from multiplying demand.
    lower_bound = record.daily_demand * 0.75
    upper_bound = record.daily_demand * 1.25
    guarded_usage_demand = min(max(usage_daily_demand, lower_bound), upper_bound)
    blended_demand = (record.daily_demand * 0.4) + (guarded_usage_demand * 0.6)
    return blended_demand, "daily-usage history (guarded against snapshot)"


def _build_explanation(
    record: ReplenishmentRecord,
    assumptions: BusinessAssumptions | None,
    stock_at_planned_delivery: float,
    incoming_available_for_delivery: int,
    reorder_point: int,
    target_stock: int,
    recommended_order_qty: int,
    needs_reorder: bool,
    priority: str,
    days_until_stockout: int | None,
    refined_daily_demand: float,
    demand_source: str,
    effective_lead_time: int,
    order_cycle_days: int,
    schedule: PlanningSchedule,
    schedule_enabled: bool,
    target_coverage_days: int,
    spoilage_limit_days: int | None,
) -> str:
    incoming_note = ""
    if record.incoming_stock:
        if incoming_available_for_delivery:
            incoming_note = (
                f" AIR counts {record.incoming_stock} unit(s) already on order only from "
                f"the next configured arrival day ({schedule.incoming_arrival_date.strftime('%A')}). "
                "Confirm the PO ETA before releasing the order."
            )
        else:
            incoming_note = (
                f" AIR does not count the {record.incoming_stock} unit(s) already on order before "
                "the planned delivery because they are not yet available on the shelf."
            )
    demand_note = (
        f" AIR used a refined demand rate of {refined_daily_demand:.2f} units/day from {demand_source}."
    )
    lead_time_note = f" AIR used an effective lead time of {effective_lead_time} day(s)."
    review_note = (
        f" AIR plans this order for {schedule.next_order_date.strftime('%A')} and expects it to be "
        f"available on {schedule.planned_delivery_date.strftime('%A')}."
        if schedule_enabled
        else f" AIR used a delivery cycle of about {order_cycle_days} day(s) between replenishment opportunities."
    )
    reorder_note = (
        (
            f" AIR set the reorder point from the {max((schedule.planned_delivery_date - date.today()).days, 0)} "
            "day(s) until that planned delivery plus safety stock."
            if schedule_enabled
            else f" AIR set the reorder point from {effective_lead_time} day(s) of demand plus safety stock."
        )
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
            f"is projected to have {max(ceil(stock_at_planned_delivery), 0)} unit(s) available at the planned delivery, "
            f"below the target level "
            f"({target_stock}). {stockout_window} Recommended order quantity: "
            f"{recommended_order_qty}.{incoming_note} {demand_note}{lead_time_note} {review_note} {reorder_note} {sizing_note} "
            f"{spoilage_note}{zero_demand_note}{notes_note}"
        )

    no_order_intro = "No order is needed for the next planned cycle." if schedule_enabled else "No immediate reorder needed."
    return (
        f"{no_order_intro} {record.name} is projected to have "
        f"{max(ceil(stock_at_planned_delivery), 0)} unit(s) available at the planned delivery, covering the target "
        f"({target_stock}).{incoming_note} "
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


def _planning_schedule(
    today: date,
    base_lead_time: int,
    assumptions: BusinessAssumptions | None,
    fallback_cycle_days: int,
) -> PlanningSchedule:
    order_days = assumptions.order_days if assumptions else []
    arrival_days = assumptions.arrival_days if assumptions else []
    next_order_date = _next_scheduled_date(today, order_days) if order_days else today

    if not arrival_days:
        planned_delivery_date = next_order_date + timedelta(days=base_lead_time)
        return PlanningSchedule(
            next_order_date=next_order_date,
            planned_delivery_date=planned_delivery_date,
            incoming_arrival_date=today + timedelta(days=base_lead_time),
            next_delivery_gap_days=fallback_cycle_days,
        )

    # A supplier delivery must occur on an allowed arrival day after the order lead time.
    planned_delivery_date = _next_scheduled_date(
        next_order_date + timedelta(days=base_lead_time), arrival_days
    )
    incoming_arrival_date = _next_scheduled_date(today, arrival_days)
    following_delivery_date = _next_scheduled_date(
        planned_delivery_date + timedelta(days=1), arrival_days
    )
    return PlanningSchedule(
        next_order_date=next_order_date,
        planned_delivery_date=planned_delivery_date,
        incoming_arrival_date=incoming_arrival_date,
        next_delivery_gap_days=(following_delivery_date - planned_delivery_date).days,
    )


def _next_scheduled_date(start: date, days: list[str]) -> date:
    weekday_indexes = {WEEKDAYS[day] for day in days if day in WEEKDAYS}
    if not weekday_indexes:
        return start
    for offset in range(8):
        candidate = start + timedelta(days=offset)
        if candidate.weekday() in weekday_indexes:
            return candidate
    return start


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
