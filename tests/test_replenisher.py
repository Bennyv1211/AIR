from datetime import date

from app.models import BusinessAssumptions, ReplenishmentRecord
from app.services.questionnaire import classify_item
from app.services.replenisher import _planning_schedule, build_recommendation, build_recommendations


def test_build_recommendation_flags_reorder_and_uses_minimum_order_quantity() -> None:
    record = ReplenishmentRecord(
        sku="SKU-1",
        name="Widget",
        current_stock=5,
        daily_demand=3,
        lead_time_days=4,
        safety_stock=5,
        min_order_qty=20,
        incoming_stock=0,
    )

    recommendation = build_recommendation(record)

    assert recommendation.needs_reorder is True
    assert recommendation.recommended_order_qty >= 20
    assert recommendation.priority in {"high", "critical", "medium"}
    assert recommendation.projected_stockout_date is not None
    assert "Reorder now." in recommendation.explanation
    assert recommendation.demand_source == "snapshot"


def test_build_recommendations_sorts_urgent_items_first() -> None:
    low = ReplenishmentRecord(
        sku="SKU-LOW",
        name="Low Priority",
        current_stock=100,
        daily_demand=2,
        lead_time_days=3,
        safety_stock=2,
        min_order_qty=5,
        incoming_stock=0,
    )
    critical = ReplenishmentRecord(
        sku="SKU-CRIT",
        name="Critical",
        current_stock=0,
        daily_demand=4,
        lead_time_days=5,
        safety_stock=8,
        min_order_qty=10,
        incoming_stock=0,
    )

    recommendations = build_recommendations([low, critical])

    assert recommendations[0].sku == "SKU-CRIT"
    assert recommendations[0].priority == "critical"


def test_build_recommendation_explains_when_no_reorder_is_needed() -> None:
    record = ReplenishmentRecord(
        sku="SKU-SAFE",
        name="Safe Item",
        current_stock=100,
        daily_demand=2,
        lead_time_days=3,
        safety_stock=5,
        min_order_qty=10,
        incoming_stock=0,
    )

    recommendation = build_recommendation(record)

    assert recommendation.needs_reorder is False
    assert recommendation.explanation.startswith("No immediate reorder needed.")


def test_build_recommendation_uses_incoming_stock_in_analysis() -> None:
    record = ReplenishmentRecord(
        sku="SKU-INCOMING",
        name="Incoming Item",
        current_stock=4,
        daily_demand=2,
        lead_time_days=3,
        safety_stock=4,
        min_order_qty=10,
        incoming_stock=20,
    )

    recommendation = build_recommendation(record)

    assert recommendation.needs_reorder is False
    assert "already on order" in recommendation.explanation


def test_build_recommendation_can_use_daily_usage_override() -> None:
    record = ReplenishmentRecord(
        sku="SKU-TREND",
        name="Trend Item",
        current_stock=20,
        daily_demand=2,
        lead_time_days=4,
        safety_stock=3,
        min_order_qty=10,
        incoming_stock=0,
    )

    recommendation = build_recommendation(record, {"SKU-TREND": 6})

    assert recommendation.demand_source.startswith("daily-usage history")
    assert "refined demand rate" in recommendation.explanation
    assert recommendation.reorder_point == 13


def test_build_recommendation_lets_usage_history_drive_demand_when_available() -> None:
    record = ReplenishmentRecord(
        sku="SKU-USAGE-FIRST",
        name="Usage First Item",
        current_stock=30,
        daily_demand=1,
        lead_time_days=2,
        safety_stock=4,
        min_order_qty=10,
        incoming_stock=0,
    )

    recommendation = build_recommendation(record, {"SKU-USAGE-FIRST": 7})

    assert recommendation.demand_source.startswith("daily-usage history")
    assert recommendation.reorder_point == 7
    assert recommendation.target_stock == 13
    assert recommendation.needs_reorder is False


def test_usage_history_is_guarded_by_the_inventory_snapshot_demand() -> None:
    record = ReplenishmentRecord(
        sku="SKU-GUARDED",
        name="Guarded Demand Item",
        current_stock=100,
        daily_demand=10,
        lead_time_days=2,
    )

    recommendation = build_recommendation(record, {"SKU-GUARDED": 50})

    # The usage signal is capped at 125% of the BPS run rate, then blended at 60%.
    assert "guarded against snapshot" in recommendation.demand_source
    assert recommendation.reorder_point == 23


def test_build_recommendation_handles_zero_demand_without_failing() -> None:
    record = ReplenishmentRecord(
        sku="SKU-DORMANT",
        name="Dormant Item",
        current_stock=10,
        daily_demand=0,
        lead_time_days=5,
        safety_stock=0,
        min_order_qty=10,
        incoming_stock=0,
    )

    recommendation = build_recommendation(record)

    assert recommendation.needs_reorder is False
    assert recommendation.days_until_stockout is None
    assert "no active demand" in recommendation.explanation.lower()


def test_build_recommendation_uses_spoilage_limit_for_herbs() -> None:
    record = ReplenishmentRecord(
        sku="SKU-HERB",
        name="Fresh Basil",
        current_stock=1,
        daily_demand=5,
        lead_time_days=2,
        safety_stock=1,
        min_order_qty=0,
        incoming_stock=0,
    )
    assumptions = BusinessAssumptions(
        shipping_days_per_week=5,
        arrival_days=["Monday", "Wednesday", "Friday"],
        default_spoilage_days=10,
        herb_spoilage_days=2,
    )

    recommendation = build_recommendation(record, assumptions=assumptions)

    assert "perishable" in recommendation.explanation.lower()
    assert recommendation.target_stock <= recommendation.reorder_point + 10


def test_build_recommendation_treats_canned_goods_as_dry_by_default() -> None:
    record = ReplenishmentRecord(
        sku="SKU-DRY",
        name="Canned Black Beans",
        current_stock=2,
        daily_demand=3,
        lead_time_days=2,
        safety_stock=1,
        min_order_qty=0,
        incoming_stock=0,
    )

    recommendation = build_recommendation(record)

    assert "perishable" not in recommendation.explanation.lower()


def test_build_recommendation_mentions_additional_notes() -> None:
    record = ReplenishmentRecord(
        sku="SKU-NOTE",
        name="Fresh Basil",
        current_stock=2,
        daily_demand=2,
        lead_time_days=2,
        safety_stock=1,
        min_order_qty=0,
        incoming_stock=0,
    )
    assumptions = BusinessAssumptions(
        shipping_days_per_week=5,
        arrival_days=["Monday", "Wednesday"],
        herb_spoilage_days=2,
        additional_notes="These herbs often arrive with spoilage claims.",
    )

    recommendation = build_recommendation(record, assumptions=assumptions)

    assert "spoilage claims" in recommendation.explanation.lower()


def test_build_recommendation_skips_order_when_effective_stock_covers_cycle_and_safety() -> None:
    record = ReplenishmentRecord(
        sku="SKU-COVERED",
        name="Covered Item",
        current_stock=100,
        daily_demand=90 / 7,
        lead_time_days=1,
        safety_stock=30,
        min_order_qty=10,
        incoming_stock=20,
    )

    recommendation = build_recommendation(record)

    assert recommendation.reorder_point == 43
    assert recommendation.needs_reorder is False
    assert recommendation.recommended_order_qty == 0
    assert "No immediate reorder needed." in recommendation.explanation


def test_build_recommendation_sizes_order_for_current_delivery_cycle_only() -> None:
    record = ReplenishmentRecord(
        sku="SKU-CYCLE",
        name="Cycle Planned Item",
        current_stock=5,
        daily_demand=10,
        lead_time_days=1,
        safety_stock=5,
        min_order_qty=0,
        incoming_stock=0,
    )
    assumptions = BusinessAssumptions(
        shipping_days_per_week=3,
    )

    recommendation = build_recommendation(record, assumptions=assumptions)

    assert recommendation.reorder_point == 15
    assert recommendation.target_stock == 35
    assert recommendation.recommended_order_qty == 30
    assert "current delivery cycle only" in recommendation.explanation


def test_reorder_point_does_not_include_the_full_delivery_cycle() -> None:
    record = ReplenishmentRecord(
        sku="SKU-LEAN-ROP",
        name="Lean Reorder Point",
        current_stock=25,
        daily_demand=5,
        lead_time_days=2,
        safety_stock=4,
        min_order_qty=0,
        incoming_stock=0,
    )
    assumptions = BusinessAssumptions(shipping_days_per_week=2)

    recommendation = build_recommendation(record, assumptions=assumptions)

    assert recommendation.reorder_point == 14
    assert recommendation.target_stock == 24
    assert recommendation.needs_reorder is False


def test_item_classification_does_not_match_keywords_inside_other_words() -> None:
    record = ReplenishmentRecord(
        sku="SKU-NOT-SAGE",
        name="Usage First Item",
        current_stock=1,
        daily_demand=1,
        lead_time_days=1,
    )

    assert classify_item(record) == "uncertain"


def test_schedule_plans_order_and_existing_stock_across_delivery_dates() -> None:
    assumptions = BusinessAssumptions(
        order_days=["Thursday"],
        arrival_days=["Sunday"],
    )

    schedule = _planning_schedule(date(2026, 8, 31), 0, assumptions, 7)

    assert schedule.next_order_date == date(2026, 9, 3)
    assert schedule.planned_delivery_date == date(2026, 9, 6)
    assert schedule.incoming_arrival_date == date(2026, 9, 6)
    assert schedule.next_delivery_gap_days == 7
