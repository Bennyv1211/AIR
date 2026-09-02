from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class ReplenishmentRecord(BaseModel):
    sku: str = Field(..., min_length=1, description="Unique product identifier")
    name: str = Field(..., min_length=1, description="Display name for the product")
    current_stock: int = Field(..., ge=0)
    daily_demand: float = Field(..., ge=0)
    lead_time_days: int = Field(..., ge=0)
    safety_stock: int = Field(0, ge=0)
    min_order_qty: int = Field(0, ge=0)
    incoming_stock: int = Field(0, ge=0)
    supplier_code: str | None = None


class IngestRequest(BaseModel):
    source: str = Field(..., min_length=1)
    records: list[ReplenishmentRecord] = Field(..., min_length=1)


class IngestResponse(BaseModel):
    source: str
    ingested_records: int
    message: str


class FileMappingPreview(BaseModel):
    field: str
    header: str | None
    source: Literal["heuristic", "openai", "unmapped"]


class FilePreviewResponse(BaseModel):
    filename: str
    dataset_kind: Literal["replenishment", "usage"]
    headers: list[str]
    mappings: list[FileMappingPreview]
    ai_enabled: bool


class DatasetImportMetadata(BaseModel):
    source: str | None = None
    mapping_origin: Literal["heuristic", "openai", "none"] = "none"
    mapped_with_ai: bool = False


class UsageRecord(BaseModel):
    sku: str = Field(..., min_length=1)
    usage_date: date
    units_used: float = Field(..., ge=0)


class BusinessAssumptions(BaseModel):
    shipping_days_per_week: int | None = Field(None, ge=1, le=7)
    order_days: list[str] = Field(default_factory=list)
    arrival_days: list[str] = Field(default_factory=list)
    default_spoilage_days: int | None = Field(None, ge=0)
    produce_spoilage_days: int | None = Field(None, ge=0)
    herb_spoilage_days: int | None = Field(None, ge=0)
    additional_notes: str = ""


class AnalysisQuestion(BaseModel):
    id: str
    prompt: str
    help_text: str
    input_type: Literal["number", "multiselect", "textarea"]
    required: bool = True
    options: list[str] = Field(default_factory=list)


class AssumptionStateResponse(BaseModel):
    assumptions: BusinessAssumptions
    questions: list[AnalysisQuestion]
    final_verdict_ready: bool


class Recommendation(BaseModel):
    sku: str
    name: str
    reorder_point: int
    target_stock: int
    current_stock: int
    stock_gap: int
    recommended_order_qty: int
    needs_reorder: bool
    priority: Literal["low", "medium", "high", "critical"]
    days_until_stockout: int | None
    projected_stockout_date: date | None
    planned_order_date: date | None = None
    planned_delivery_date: date | None = None
    demand_source: str
    ai_refined: bool = False
    ai_note: str = ""
    supplier_code: str | None = None
    explanation: str


class RecommendationResponse(BaseModel):
    source: str | None
    total_records: int
    final_verdict_ready: bool = True
    pending_questions: list[AnalysisQuestion] = Field(default_factory=list)
    verdict_origin: Literal["analysis", "ai-assisted analysis"]
    inventory_mapping_origin: Literal["heuristic", "openai", "none"] = "none"
    usage_mapping_origin: Literal["heuristic", "openai", "none"] = "none"
    recommendations: list[Recommendation]
