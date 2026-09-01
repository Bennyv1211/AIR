from __future__ import annotations

import json
import os
from math import ceil
from typing import Any

from app.models import BusinessAssumptions, Recommendation, ReplenishmentRecord
from app.services.openai_semantic import _build_client, openai_is_configured


def refine_recommendations_with_ai(
    records: list[ReplenishmentRecord],
    recommendations: list[Recommendation],
    usage_overrides: dict[str, float],
    assumptions: BusinessAssumptions | None,
) -> tuple[list[Recommendation], bool]:
    if not _ai_planning_enabled() or not records or not recommendations:
        return recommendations, False

    client = _build_client()
    if client is None:
        return recommendations, False

    payload = _planner_payload(records, recommendations, usage_overrides, assumptions)
    try:
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
            input=_planner_prompt(payload),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "air_ai_planning_review",
                    "strict": True,
                    "schema": _planner_schema(),
                }
            },
        )
        parsed = json.loads(response.output_text)
        decisions = parsed.get("items", [])
        refined = _apply_ai_decisions(records, recommendations, usage_overrides, decisions)
        return refined, True
    except Exception:
        return recommendations, False


def _ai_planning_enabled() -> bool:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    if os.getenv("AIR_ENABLE_AI_PLANNING", "true").strip().lower() in {"0", "false", "no"}:
        return False
    return openai_is_configured()


def _planner_payload(
    records: list[ReplenishmentRecord],
    recommendations: list[Recommendation],
    usage_overrides: dict[str, float],
    assumptions: BusinessAssumptions | None,
) -> dict[str, Any]:
    record_by_sku = {record.sku: record for record in records}
    items: list[dict[str, Any]] = []
    for recommendation in recommendations[:60]:
        record = record_by_sku.get(recommendation.sku)
        if record is None:
            continue
        daily_demand_used = usage_overrides.get(record.sku, record.daily_demand)
        items.append(
            {
                "sku": record.sku,
                "name": record.name,
                "current_stock": record.current_stock,
                "incoming_stock": record.incoming_stock,
                "effective_stock": record.current_stock + record.incoming_stock,
                "daily_demand_used": round(daily_demand_used, 4),
                "lead_time_days": record.lead_time_days,
                "safety_stock": record.safety_stock,
                "min_order_qty": record.min_order_qty,
                "rule_reorder_point": recommendation.reorder_point,
                "rule_target_stock": recommendation.target_stock,
                "rule_order_now": recommendation.recommended_order_qty,
                "rule_priority": recommendation.priority,
                "planned_order_date": recommendation.planned_order_date.isoformat() if recommendation.planned_order_date else None,
                "planned_delivery_date": recommendation.planned_delivery_date.isoformat() if recommendation.planned_delivery_date else None,
                "demand_source": recommendation.demand_source,
            }
        )

    assumption_payload = {
        "shipping_days_per_week": assumptions.shipping_days_per_week if assumptions else None,
        "order_days": assumptions.order_days if assumptions else [],
        "arrival_days": assumptions.arrival_days if assumptions else [],
        "default_spoilage_days": assumptions.default_spoilage_days if assumptions else None,
        "produce_spoilage_days": assumptions.produce_spoilage_days if assumptions else None,
        "herb_spoilage_days": assumptions.herb_spoilage_days if assumptions else None,
        "additional_notes": assumptions.additional_notes if assumptions else "",
    }
    return {"assumptions": assumption_payload, "items": items}


def _planner_prompt(payload: dict[str, Any]) -> str:
    return (
        "You are reviewing replenishment recommendations for an inventory planning tool called AIR.\n"
        "Your job is to refine the current-cycle order amount for each item using the provided rules-based baseline.\n"
        "Think about outgoing daily demand, on-hand stock, incoming stock, safety stock, delivery cadence, "
        "lead time, and perishability assumptions.\n"
        "Only recommend the amount to order now for the current replenishment cycle. Do not stock up for multiple cycles.\n"
        "If the baseline recommendation already looks right, keep it.\n"
        "Return one result per item, with concise reasoning.\n"
        f"Planning context: {json.dumps(payload, ensure_ascii=True)}"
    )


def _planner_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sku": {"type": "string"},
                        "order_now_units": {"type": "integer", "minimum": 0},
                        "reason": {"type": "string"},
                    },
                    "required": ["sku", "order_now_units", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


def _apply_ai_decisions(
    records: list[ReplenishmentRecord],
    recommendations: list[Recommendation],
    usage_overrides: dict[str, float],
    decisions: list[dict[str, Any]],
) -> list[Recommendation]:
    decisions_by_sku = {
        decision.get("sku"): decision
        for decision in decisions
        if isinstance(decision.get("sku"), str)
    }
    records_by_sku = {record.sku: record for record in records}
    refined: list[Recommendation] = []

    for recommendation in recommendations:
        decision = decisions_by_sku.get(recommendation.sku)
        record = records_by_sku.get(recommendation.sku)
        if decision is None or record is None:
            refined.append(recommendation)
            continue

        daily_demand_used = usage_overrides.get(record.sku, record.daily_demand)
        safe_qty = _safe_ai_order_qty(
            recommendation,
            record,
            daily_demand_used,
            int(decision.get("order_now_units", recommendation.recommended_order_qty)),
        )
        effective_stock = record.current_stock + record.incoming_stock
        explanation = (
            f"{recommendation.explanation} AI planning note: {str(decision.get('reason', '')).strip()}"
            if str(decision.get("reason", "")).strip()
            else recommendation.explanation
        )
        ai_note = str(decision.get("reason", "")).strip()
        refined.append(
            recommendation.model_copy(
                update={
                    "recommended_order_qty": safe_qty,
                    "needs_reorder": safe_qty > 0,
                    "stock_gap": safe_qty if safe_qty > 0 else 0,
                    "target_stock": effective_stock + safe_qty,
                    "ai_refined": safe_qty != recommendation.recommended_order_qty or bool(ai_note),
                    "ai_note": ai_note,
                    "explanation": explanation,
                }
            )
        )

    return refined


def _safe_ai_order_qty(
    recommendation: Recommendation,
    record: ReplenishmentRecord,
    daily_demand_used: float,
    ai_qty: int,
) -> int:
    weekly_cushion = ceil(max(daily_demand_used, 0) * 7)
    max_allowed = max(
        recommendation.recommended_order_qty,
        recommendation.reorder_point,
        recommendation.target_stock,
        record.min_order_qty,
    ) + weekly_cushion
    bounded = min(max(ai_qty, 0), max_allowed)
    if bounded == 0:
        return 0
    return max(record.min_order_qty, bounded)
