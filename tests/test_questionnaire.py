from app.models import BusinessAssumptions, ReplenishmentRecord
from app.services.questionnaire import build_analysis_questions, classify_item


def test_build_analysis_questions_requests_shipping_arrival_and_spoilage_answers() -> None:
    records = [
        ReplenishmentRecord(
            sku="SKU-1",
            name="Fresh Basil",
            current_stock=10,
            daily_demand=2,
            lead_time_days=2,
            safety_stock=1,
            min_order_qty=0,
            incoming_stock=0,
        )
    ]

    questions = build_analysis_questions(records, BusinessAssumptions())

    question_ids = {question.id for question in questions}
    assert "shipping_days_per_week" in question_ids
    assert "arrival_days" in question_ids
    assert "default_spoilage_days" not in question_ids
    assert "herb_spoilage_days" not in question_ids
    assert "additional_notes" in question_ids


def test_classify_item_marks_herbs_and_produce() -> None:
    herb = ReplenishmentRecord(
        sku="H1",
        name="Cilantro Bunch",
        current_stock=5,
        daily_demand=1,
        lead_time_days=1,
        safety_stock=0,
        min_order_qty=0,
        incoming_stock=0,
    )
    produce = ReplenishmentRecord(
        sku="P1",
        name="Romaine Lettuce",
        current_stock=5,
        daily_demand=1,
        lead_time_days=1,
        safety_stock=0,
        min_order_qty=0,
        incoming_stock=0,
    )

    assert classify_item(herb) == "herb"
    assert classify_item(produce) == "refrigerated-produce"


def test_build_analysis_questions_only_asks_spoilage_for_uncertain_items() -> None:
    records = [
        ReplenishmentRecord(
            sku="SKU-2",
            name="Custom Farm Box",
            current_stock=4,
            daily_demand=1,
            lead_time_days=2,
            safety_stock=0,
            min_order_qty=0,
            incoming_stock=0,
        )
    ]

    questions = build_analysis_questions(records, BusinessAssumptions())

    question_ids = {question.id for question in questions}
    assert "default_spoilage_days" in question_ids


def test_additional_notes_question_is_optional() -> None:
    records = [
        ReplenishmentRecord(
            sku="SKU-3",
            name="Canned Black Beans",
            current_stock=8,
            daily_demand=1,
            lead_time_days=2,
            safety_stock=0,
            min_order_qty=0,
            incoming_stock=0,
        )
    ]

    questions = build_analysis_questions(records, BusinessAssumptions())

    additional = next(question for question in questions if question.id == "additional_notes")
    assert additional.required is False


def test_build_analysis_questions_hides_questions_after_required_answers_are_saved() -> None:
    records = [
        ReplenishmentRecord(
            sku="SKU-4",
            name="Canned Black Beans",
            current_stock=8,
            daily_demand=1,
            lead_time_days=2,
            safety_stock=0,
            min_order_qty=0,
            incoming_stock=0,
        )
    ]
    assumptions = BusinessAssumptions(
        shipping_days_per_week=5,
        arrival_days=["Monday", "Wednesday", "Friday"],
    )

    questions = build_analysis_questions(records, assumptions)

    assert questions == []


def test_build_analysis_questions_hides_optional_note_after_it_has_been_saved() -> None:
    records = [
        ReplenishmentRecord(
            sku="SKU-5",
            name="Custom Farm Box",
            current_stock=8,
            daily_demand=1,
            lead_time_days=2,
            safety_stock=0,
            min_order_qty=0,
            incoming_stock=0,
        )
    ]
    assumptions = BusinessAssumptions(
        shipping_days_per_week=5,
        arrival_days=["Monday", "Wednesday", "Friday"],
        default_spoilage_days=4,
        additional_notes="Supplier substitutions happen often.",
    )

    questions = build_analysis_questions(records, assumptions)

    assert questions == []
