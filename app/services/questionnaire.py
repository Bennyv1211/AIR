from __future__ import annotations

import re

from app.models import AnalysisQuestion, BusinessAssumptions, ReplenishmentRecord

WEEKDAY_OPTIONS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]

REFRIGERATED_PRODUCE_KEYWORDS = {
    "lettuce",
    "spinach",
    "kale",
    "arugula",
    "cilantro",
    "basil",
    "mint",
    "dill",
    "parsley",
    "oregano",
    "thyme",
    "rosemary",
    "chive",
    "sage",
    "greens",
    "cucumber",
    "pepper",
    "berry",
    "mushroom",
}

HERB_KEYWORDS = {
    "basil",
    "cilantro",
    "mint",
    "dill",
    "parsley",
    "oregano",
    "thyme",
    "rosemary",
    "chive",
    "sage",
    "herb",
}

PRODUCE_KEYWORDS = {
    "lettuce",
    "spinach",
    "kale",
    "arugula",
    "tomato",
    "cucumber",
    "pepper",
    "onion",
    "carrot",
    "produce",
    "fruit",
    "vegetable",
    "greens",
    "avocado",
    "berry",
}

DRY_GOODS_KEYWORDS = {
    "can",
    "canned",
    "beans",
    "rice",
    "pasta",
    "flour",
    "sugar",
    "salt",
    "oil",
    "vinegar",
    "soup",
    "tomato sauce",
    "broth",
    "tuna",
}


def build_analysis_questions(
    records: list[ReplenishmentRecord],
    assumptions: BusinessAssumptions,
) -> list[AnalysisQuestion]:
    if not records:
        return []

    questions: list[AnalysisQuestion] = []
    has_uncertain_items = any(classify_item(record) == "uncertain" for record in records)

    if assumptions.shipping_days_per_week is None:
        questions.append(
            AnalysisQuestion(
                id="shipping_days_per_week",
                prompt="How many days per week do you ship or receive replenishment orders?",
                help_text="AIR uses this to understand how often you can replenish stock.",
                input_type="number",
            )
        )

    if not assumptions.arrival_days:
        questions.append(
            AnalysisQuestion(
                id="arrival_days",
                prompt="Which days do your deliveries usually arrive?",
                help_text="Arrival days help AIR avoid recommending stock that would arrive too late.",
                input_type="multiselect",
                options=WEEKDAY_OPTIONS,
            )
        )

    if has_uncertain_items and assumptions.default_spoilage_days is None:
        questions.append(
            AnalysisQuestion(
                id="default_spoilage_days",
                prompt="For items AIR could not confidently classify, how many days do they usually last before spoilage?",
                help_text="AIR already infers herbs, refrigerated produce, and dry goods when it is confident. Use this only as a fallback for unclear items.",
                input_type="number",
            )
        )

    required_questions = [question for question in questions if question.required]
    if required_questions and not assumptions.additional_notes.strip():
        questions.append(
            AnalysisQuestion(
                id="additional_notes",
                prompt="Is there anything else AIR should know before it gives the final verdict?",
                help_text="Optional: note issues like spoiled arrivals, frequent claims, supplier substitutions, or anything unusual about these items.",
                input_type="textarea",
                required=False,
            )
        )

    return questions


def classify_item(record: ReplenishmentRecord) -> str:
    name = record.name.lower()
    if _matches_keywords(name, HERB_KEYWORDS):
        return "herb"
    if _matches_keywords(name, REFRIGERATED_PRODUCE_KEYWORDS):
        return "refrigerated-produce"
    if _matches_keywords(name, PRODUCE_KEYWORDS):
        return "produce"
    if _matches_keywords(name, DRY_GOODS_KEYWORDS):
        return "dry"
    return "uncertain"


def _matches_keywords(text: str, keywords: set[str]) -> bool:
    lowered = text.lower()
    return any(re.search(rf"\b{re.escape(keyword)}\b", lowered) for keyword in keywords)
