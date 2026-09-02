from __future__ import annotations

import json
import os
import re
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
                "supplier_code": record.supplier_code,
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
        "The rules-based plan is the safety baseline. Keep it unless there is a clear, data-supported reason to adjust it; "
        "never treat unconfirmed incoming stock as guaranteed.\n"
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
        ai_note = str(decision.get("reason", "")).strip()
        explanation = _ai_final_explanation(recommendation.explanation, safe_qty, ai_note)
        refined.append(
            recommendation.model_copy(
                update={
                    "recommended_order_qty": safe_qty,
                    "needs_reorder": safe_qty > 0,
                    "stock_gap": safe_qty if safe_qty > 0 else 0,
                    # Preserve the rules-based target. An AI review changes the current order,
                    # not the meaning of the target-stock column.
                    "priority": recommendation.priority if safe_qty > 0 else "low",
                    "ai_refined": safe_qty != recommendation.recommended_order_qty or bool(ai_note),
                    "ai_note": ai_note,
                    "explanation": explanation,
                }
            )
        )

    return refined


def _ai_final_explanation(baseline: str, order_qty: int, ai_note: str) -> str:
    if order_qty > 0:
        decision = f"Final decision: reorder {order_qty} unit(s) for the current cycle."
    else:
        decision = "Final decision: no order is needed for the current cycle."

    for prefix in (
        "Reorder now.",
        "No immediate reorder needed.",
        "No order is needed for the next planned cycle.",
    ):
        if baseline.startswith(prefix):
            baseline = baseline[len(prefix):].lstrip()
            break

    # The rules explanation contains its original suggested quantity. Once AI safely
    # refines it, showing both values makes the final verdict ambiguous.
    baseline = re.sub(r"\s*Recommended order quantity:\s*\d+(?:\.\d+)?\.", "", baseline)

    note = f" AI planning note: {ai_note}" if ai_note else ""
    return f"{decision} {baseline}{note}".strip()


def _safe_ai_order_qty(
    recommendation: Recommendation,
    record: ReplenishmentRecord,
    daily_demand_used: float,
    ai_qty: int,
) -> int:
    baseline_qty = recommendation.recommended_order_qty
    if baseline_qty <= 0:
        # An LLM must not create a purchase where the schedule-aware safety rules say no.
        return 0

    # AI can fine-tune the current order, but cannot erase or materially inflate a
    # schedule-backed order without a human changing the inputs.
    variance = max(1, ceil(baseline_qty * 0.15))
    lower_bound = max(record.min_order_qty, baseline_qty - variance)
    upper_bound = baseline_qty + variance
    return min(max(ai_qty, lower_bound), upper_bound)
