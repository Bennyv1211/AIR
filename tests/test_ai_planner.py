from app.models import Recommendation, ReplenishmentRecord
from app.services.openai_planner import _apply_ai_decisions


def test_apply_ai_decisions_updates_order_qty_and_explanation() -> None:
    record = ReplenishmentRecord(
        sku="SKU-AI-1",
        name="AI Planned Item",
        current_stock=8,
        daily_demand=4,
        lead_time_days=2,
        safety_stock=4,
        min_order_qty=5,
        incoming_stock=3,
    )
    recommendation = Recommendation(
        sku="SKU-AI-1",
        name="AI Planned Item",
        reorder_point=20,
        target_stock=20,
        current_stock=8,
        stock_gap=9,
        recommended_order_qty=9,
        needs_reorder=True,
        priority="high",
        days_until_stockout=3,
        projected_stockout_date=None,
        demand_source="daily-usage history",
        explanation="Rules-based plan.",
    )

    refined = _apply_ai_decisions(
        [record],
        [recommendation],
        {"SKU-AI-1": 4},
        [{"sku": "SKU-AI-1", "order_now_units": 12, "reason": "Demand is climbing into the next arrival window."}],
    )

    assert refined[0].recommended_order_qty == 11
    assert refined[0].needs_reorder is True
    assert refined[0].target_stock == 20
    assert refined[0].ai_refined is True
    assert refined[0].ai_note == "Demand is climbing into the next arrival window."
    assert "AI planning note" in refined[0].explanation


def test_apply_ai_decisions_keeps_a_rules_required_order_when_ai_says_zero() -> None:
    record = ReplenishmentRecord(
        sku="SKU-AI-3",
        name="Incoming Supply Covers Demand",
        current_stock=20,
        daily_demand=2,
        lead_time_days=2,
        safety_stock=3,
        min_order_qty=0,
        incoming_stock=20,
    )
    recommendation = Recommendation(
        sku="SKU-AI-3",
        name="Incoming Supply Covers Demand",
        reorder_point=15,
        target_stock=22,
        current_stock=20,
        stock_gap=5,
        recommended_order_qty=5,
        needs_reorder=True,
        priority="high",
        days_until_stockout=5,
        projected_stockout_date=None,
        demand_source="daily-usage history",
        explanation="Reorder now. Incoming Supply Covers Demand is below the target level.",
    )

    refined = _apply_ai_decisions(
        [record],
        [recommendation],
        {"SKU-AI-3": 2},
        [{"sku": "SKU-AI-3", "order_now_units": 0, "reason": "Incoming stock covers this cycle."}],
    )

    assert refined[0].recommended_order_qty == 4
    assert refined[0].needs_reorder is True
    assert refined[0].priority == "high"
    assert refined[0].explanation.startswith("Final decision: reorder 4")


def test_apply_ai_decisions_clamps_extreme_ai_order_qty() -> None:
    record = ReplenishmentRecord(
        sku="SKU-AI-2",
        name="Clamped Item",
        current_stock=4,
        daily_demand=2,
        lead_time_days=2,
        safety_stock=2,
        min_order_qty=0,
        incoming_stock=0,
    )
    recommendation = Recommendation(
        sku="SKU-AI-2",
        name="Clamped Item",
        reorder_point=10,
        target_stock=10,
        current_stock=4,
        stock_gap=6,
        recommended_order_qty=6,
        needs_reorder=True,
        priority="medium",
        days_until_stockout=2,
        projected_stockout_date=None,
        demand_source="snapshot",
        explanation="Rules-based plan.",
    )

    refined = _apply_ai_decisions(
        [record],
        [recommendation],
        {"SKU-AI-2": 2},
        [{"sku": "SKU-AI-2", "order_now_units": 999, "reason": "Huge suggestion."}],
    )

    assert refined[0].recommended_order_qty == 7
    assert refined[0].ai_refined is True


def test_apply_ai_decisions_cannot_zero_a_required_rules_order() -> None:
    record = ReplenishmentRecord(
        sku="SKU-AI-SAFE",
        name="Rules Protected Item",
        current_stock=1,
        daily_demand=4,
        lead_time_days=2,
    )
    recommendation = Recommendation(
        sku="SKU-AI-SAFE",
        name="Rules Protected Item",
        reorder_point=12,
        target_stock=12,
        current_stock=1,
        stock_gap=10,
        recommended_order_qty=10,
        needs_reorder=True,
        priority="high",
        days_until_stockout=1,
        projected_stockout_date=None,
        demand_source="snapshot",
        explanation="Reorder now. Rules Protected Item. Recommended order quantity: 10.",
    )

    refined = _apply_ai_decisions(
        [record], [recommendation], {},
        [{"sku": "SKU-AI-SAFE", "order_now_units": 0, "reason": "Skip it."}],
    )

    assert refined[0].recommended_order_qty == 8
    assert "Recommended order quantity" not in refined[0].explanation
