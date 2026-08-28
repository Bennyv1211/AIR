from __future__ import annotations

import json
import os
from typing import Any


def openai_is_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def infer_semantic_headers(
    headers: list[str],
    dataset_kind: str,
    local_candidates: dict[str, str],
) -> dict[str, str] | None:
    if not openai_is_configured():
        return None

    client = _build_client()
    if client is None:
        return None

    schema = _mapping_schema(dataset_kind)
    prompt = _mapping_prompt(headers, dataset_kind, local_candidates)

    try:
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
            input=prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "air_header_mapping",
                    "strict": True,
                    "schema": schema,
                }
            },
        )
        parsed = json.loads(response.output_text)
        mappings = parsed.get("mappings", {})
        return {
            key: value
            for key, value in mappings.items()
            if isinstance(value, str) and value in headers
        }
    except Exception:
        return None


def _build_client() -> Any | None:
    try:
        from openai import OpenAI
    except ImportError:
        return None
    return OpenAI()


def _mapping_prompt(
    headers: list[str],
    dataset_kind: str,
    local_candidates: dict[str, str],
) -> str:
    fields = {
        "replenishment": [
            "sku",
            "name",
            "current_stock",
            "daily_demand",
            "lead_time_days",
            "safety_stock",
            "min_order_qty",
            "incoming_stock",
        ],
        "usage": [
            "sku",
            "usage_date",
            "units_used",
        ],
    }[dataset_kind]

    return (
        "You map spreadsheet headers to business fields for inventory replenishment software.\n"
        "Choose only from the provided headers.\n"
        "Return null for fields that are not represented.\n"
        "Use semantic understanding for messy business labels like on hand, on order, arriving, "
        "sales last week, units sold, vendor lead time, moq, buffer stock, and similar variants.\n"
        f"Dataset kind: {dataset_kind}\n"
        f"Headers: {headers}\n"
        f"Fields to map: {fields}\n"
        f"Local heuristic candidates: {local_candidates}\n"
    )


def _mapping_schema(dataset_kind: str) -> dict[str, Any]:
    fields = {
        "replenishment": [
            "sku",
            "name",
            "current_stock",
            "daily_demand",
            "lead_time_days",
            "safety_stock",
            "min_order_qty",
            "incoming_stock",
        ],
        "usage": [
            "sku",
            "usage_date",
            "units_used",
        ],
    }[dataset_kind]

    properties = {field: {"type": ["string", "null"]} for field in fields}
    return {
        "type": "object",
        "properties": {
            "mappings": {
                "type": "object",
                "properties": properties,
                "required": fields,
                "additionalProperties": False,
            }
        },
        "required": ["mappings"],
        "additionalProperties": False,
    }
