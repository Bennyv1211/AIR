from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_data_store_path, load_env_file
from app.models import (
    AssumptionStateResponse,
    BusinessAssumptions,
    FilePreviewResponse,
    IngestRequest,
    IngestResponse,
    RecommendationResponse,
)
from app.services.exports import build_recommendations_workbook
from app.services.file_ingest import (
    parse_replenishment_file,
    parse_replenishment_file_with_metadata,
    parse_usage_file,
    parse_usage_file_with_metadata,
    preview_replenishment_file,
    preview_usage_file,
)
from app.services.openai_planner import refine_recommendations_with_ai
from app.services.questionnaire import build_analysis_questions
from app.services.replenisher import build_recommendations
from app.services.usage_trends import build_usage_overrides
from app.storage import JsonDataStore
from io import BytesIO

load_env_file()

app = FastAPI(
    title="AIR API",
    description="Artificial Intelligence Replenisher MVP",
    version="0.1.0",
)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

store = JsonDataStore(get_data_store_path())


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    return HTMLResponse(_dashboard_html())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/datasets/ingest", response_model=IngestResponse)
def ingest_dataset(payload: IngestRequest) -> IngestResponse:
    store.save_records(payload.source, payload.records)
    return IngestResponse(
        source=payload.source,
        ingested_records=len(payload.records),
        message="Dataset ingested successfully.",
    )


@app.post("/datasets/ingest-file", response_model=IngestResponse)
async def ingest_dataset_file(
    source: str = Form(...),
    file: UploadFile = File(...),
) -> IngestResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Please upload a .csv or .xlsx file.")

    try:
        content = await file.read()
        records, metadata = parse_replenishment_file_with_metadata(file.filename, content)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    metadata.source = source
    store.save_records(source, records, metadata)
    return IngestResponse(
        source=source,
        ingested_records=len(records),
        message="Dataset ingested successfully.",
    )


@app.post("/datasets/preview-file", response_model=FilePreviewResponse)
async def preview_dataset_file(file: UploadFile = File(...)) -> FilePreviewResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Please upload a .csv or .xlsx file.")
    try:
        content = await file.read()
        return preview_replenishment_file(file.filename, content)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/datasets/ingest-usage-file", response_model=IngestResponse)
async def ingest_usage_file(
    source: str = Form(...),
    file: UploadFile = File(...),
) -> IngestResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Please upload a .csv or .xlsx file.")

    try:
        content = await file.read()
        usage_records, metadata = parse_usage_file_with_metadata(file.filename, content)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    metadata.source = source
    store.save_usage_records(source, usage_records, metadata)
    return IngestResponse(
        source=source,
        ingested_records=len(usage_records),
        message="Daily usage dataset ingested successfully.",
    )


@app.post("/datasets/preview-usage-file", response_model=FilePreviewResponse)
async def preview_usage_dataset_file(file: UploadFile = File(...)) -> FilePreviewResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Please upload a .csv or .xlsx file.")
    try:
        content = await file.read()
        return preview_usage_file(file.filename, content)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/analysis/questions", response_model=AssumptionStateResponse)
def analysis_questions() -> AssumptionStateResponse:
    _, records = store.load_records()
    assumptions = store.load_assumptions()
    questions = build_analysis_questions(records, assumptions)
    return AssumptionStateResponse(
        assumptions=assumptions,
        questions=questions,
        final_verdict_ready=not _required_questions(questions),
    )


@app.post("/analysis/assumptions", response_model=AssumptionStateResponse)
def save_analysis_assumptions(payload: BusinessAssumptions) -> AssumptionStateResponse:
    merged = _merge_assumptions(store.load_assumptions(), payload)
    store.save_assumptions(merged)
    _, records = store.load_records()
    questions = build_analysis_questions(records, merged)
    return AssumptionStateResponse(
        assumptions=merged,
        questions=questions,
        final_verdict_ready=not _required_questions(questions),
    )


@app.post("/analysis/assumptions/reset", response_model=AssumptionStateResponse)
def reset_analysis_assumptions() -> AssumptionStateResponse:
    cleared = BusinessAssumptions()
    store.save_assumptions(cleared)
    _, records = store.load_records()
    questions = build_analysis_questions(records, cleared)
    return AssumptionStateResponse(
        assumptions=cleared,
        questions=questions,
        final_verdict_ready=not _required_questions(questions),
    )


@app.get("/recommendations", response_model=RecommendationResponse)
def recommendations() -> RecommendationResponse:
    source, records = store.load_records()
    _, usage_records = store.load_usage_records()
    inventory_metadata = store.load_inventory_metadata()
    usage_metadata = store.load_usage_metadata()
    assumptions = store.load_assumptions()
    questions = build_analysis_questions(records, assumptions)
    usage_overrides = build_usage_overrides(usage_records)
    if _required_questions(questions):
        verdict_origin = "ai-assisted analysis" if (
            inventory_metadata.mapped_with_ai or usage_metadata.mapped_with_ai
        ) else "analysis"
        return RecommendationResponse(
            source=source,
            total_records=len(records),
            final_verdict_ready=False,
            pending_questions=questions,
            verdict_origin=verdict_origin,
            inventory_mapping_origin=inventory_metadata.mapping_origin,
            usage_mapping_origin=usage_metadata.mapping_origin,
            recommendations=[],
        )
    base_recommendations = build_recommendations(records, usage_overrides, assumptions)
    refined_recommendations, ai_planning_used = refine_recommendations_with_ai(
        records,
        base_recommendations,
        usage_overrides,
        assumptions,
    )
    verdict_origin = "ai-assisted analysis" if (
        inventory_metadata.mapped_with_ai or usage_metadata.mapped_with_ai or ai_planning_used
    ) else "analysis"
    return RecommendationResponse(
        source=source,
        total_records=len(records),
        final_verdict_ready=True,
        pending_questions=[],
        verdict_origin=verdict_origin,
        inventory_mapping_origin=inventory_metadata.mapping_origin,
        usage_mapping_origin=usage_metadata.mapping_origin,
        recommendations=refined_recommendations,
    )


@app.get("/recommendations/export")
def export_recommendations() -> StreamingResponse:
    source, records = store.load_records()
    _, usage_records = store.load_usage_records()
    assumptions = store.load_assumptions()
    questions = build_analysis_questions(records, assumptions)
    if _required_questions(questions):
        raise HTTPException(
            status_code=400,
            detail="AIR still needs required planning answers before it can export the final verdict.",
        )

    usage_overrides = build_usage_overrides(usage_records)
    recommendations = build_recommendations(records, usage_overrides, assumptions)
    recommendations, _ = refine_recommendations_with_ai(
        records,
        recommendations,
        usage_overrides,
        assumptions,
    )
    workbook_bytes = build_recommendations_workbook(recommendations, assumptions)
    filename_root = source or "air-verdict"
    safe_name = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in filename_root)
    return StreamingResponse(
        BytesIO(workbook_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}-verdict.xlsx"'},
    )


def _required_questions(questions: list) -> list:
    return [question for question in questions if getattr(question, "required", False)]


def _merge_assumptions(
    existing: BusinessAssumptions,
    incoming: BusinessAssumptions,
) -> BusinessAssumptions:
    return BusinessAssumptions(
        shipping_days_per_week=(
            incoming.shipping_days_per_week
            if incoming.shipping_days_per_week is not None
            else existing.shipping_days_per_week
        ),
        arrival_days=incoming.arrival_days or existing.arrival_days,
        default_spoilage_days=(
            incoming.default_spoilage_days
            if incoming.default_spoilage_days is not None
            else existing.default_spoilage_days
        ),
        produce_spoilage_days=(
            incoming.produce_spoilage_days
            if incoming.produce_spoilage_days is not None
            else existing.produce_spoilage_days
        ),
        herb_spoilage_days=(
            incoming.herb_spoilage_days
            if incoming.herb_spoilage_days is not None
            else existing.herb_spoilage_days
        ),
        additional_notes=(
            incoming.additional_notes.strip()
            if incoming.additional_notes.strip()
            else existing.additional_notes
        ),
    )


def _dashboard_html() -> str:
    return """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AIR Dashboard</title>
  <style>
    :root {
      --bg: #f5f1e8;
      --bg-deep: #ebe2d2;
      --panel: rgba(255, 252, 247, 0.92);
      --panel-strong: #fffaf2;
      --ink: #1b2a25;
      --muted: #637068;
      --accent: #195f52;
      --accent-strong: #0f4c41;
      --accent-soft: #d8ebe4;
      --sand: #ede2d0;
      --sand-strong: #decdb2;
      --critical: #b24b45;
      --high: #d17d20;
      --medium: #3e79c9;
      --low: #53815a;
      --line: rgba(27, 42, 37, 0.11);
      --line-strong: rgba(27, 42, 37, 0.2);
      --shadow: 0 30px 80px rgba(43, 49, 45, 0.12);
      --shadow-soft: 0 16px 40px rgba(43, 49, 45, 0.08);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      font-family: "Segoe UI", "Trebuchet MS", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at 0% 0%, rgba(25, 95, 82, 0.24), transparent 28%),
        radial-gradient(circle at 100% 0%, rgba(209, 125, 32, 0.14), transparent 24%),
        linear-gradient(160deg, #fbf8f2 0%, var(--bg) 52%, var(--bg-deep) 100%);
      min-height: 100vh;
    }

    .app-shell {
      width: min(1320px, calc(100% - 20px));
      margin: 12px auto;
      padding: 14px;
      border: 1px solid rgba(255, 255, 255, 0.65);
      border-radius: 24px;
      background: rgba(255, 249, 241, 0.7);
      backdrop-filter: blur(12px);
      box-shadow: var(--shadow);
    }

    .topbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 14px;
      margin-bottom: 12px;
      padding: 12px 14px;
      border-radius: 20px;
      background: rgba(255, 252, 247, 0.76);
      border: 1px solid rgba(255, 255, 255, 0.55);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.7);
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 16px;
      min-width: 0;
      flex: 1 1 auto;
    }

    .brand-logo {
      width: min(360px, 100%);
      height: auto;
      display: block;
      flex: 0 1 360px;
      object-fit: contain;
    }

    .brand-copy {
      min-width: 0;
    }

    .brand-copy strong {
      display: block;
      font-size: 18px;
      letter-spacing: 0.02em;
      line-height: 1.1;
    }

    .brand-copy span {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }

    .topbar-meta {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      justify-content: flex-end;
      flex: 0 0 auto;
    }

    .meta-pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 14px;
      border-radius: 999px;
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      background: #f4ebdf;
      color: var(--ink);
      border: 1px solid rgba(27, 42, 37, 0.08);
    }

    .meta-pill.live {
      background: var(--accent-soft);
      color: var(--accent-strong);
    }

    .meta-pill.ai {
      background: rgba(62, 121, 201, 0.12);
      color: var(--medium);
    }

    .workspace {
      display: grid;
      gap: 14px;
      grid-template-columns: 250px minmax(0, 1fr);
    }

    .sidebar, .panel {
      background: var(--panel);
      border-radius: 20px;
      border: 1px solid var(--line);
      padding: 18px;
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.6), 0 14px 36px rgba(43, 49, 45, 0.04);
    }

    .sidebar {
      display: grid;
      gap: 12px;
      align-content: start;
      position: sticky;
      top: 12px;
      height: fit-content;
    }

    .eyebrow {
      font-size: 11px;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--accent);
      margin: 0 0 10px;
    }

    .sidebar-title {
      margin: 0;
      font-size: 28px;
      line-height: 0.92;
      font-family: Georgia, "Times New Roman", serif;
      font-weight: 700;
    }

    .sidebar-copy {
      margin: 0;
      color: var(--muted);
      line-height: 1.65;
      font-size: 13px;
    }

    .sidebar-section {
      padding: 14px;
      border-radius: 16px;
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.58), rgba(247, 240, 228, 0.82));
      border: 1px solid rgba(27, 42, 37, 0.08);
    }

    .signal-grid {
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }

    .signal-card {
      padding: 12px 14px;
      border-radius: 14px;
      background: var(--panel-strong);
      border: 1px solid var(--line);
    }

    .signal-card strong {
      display: block;
      font-size: 13px;
      margin-bottom: 4px;
    }

    .signal-card span {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }

    .content-stack {
      display: grid;
      gap: 14px;
    }

    .workflow-strip {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }

    .workflow-card {
      padding: 14px;
      border-radius: 16px;
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.82), rgba(246, 238, 225, 0.94));
      border: 1px solid rgba(27, 42, 37, 0.08);
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.72);
    }

    .workflow-step {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 26px;
      height: 26px;
      border-radius: 999px;
      margin-bottom: 8px;
      background: var(--accent-soft);
      color: var(--accent-strong);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
    }

    .workflow-card strong {
      display: block;
      margin-bottom: 6px;
      font-size: 14px;
    }

    .workflow-card span {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }

    .hero-board {
      display: grid;
      gap: 14px;
      grid-template-columns: minmax(0, 1fr) minmax(340px, 0.9fr);
      align-items: stretch;
    }

    .hero-card {
      position: relative;
      overflow: hidden;
      min-height: 0;
      background:
        radial-gradient(circle at top right, rgba(25, 95, 82, 0.16), transparent 34%),
        linear-gradient(180deg, rgba(255, 253, 249, 0.94), rgba(248, 241, 229, 0.92));
    }

    .hero-card::after {
      content: "";
      position: absolute;
      right: -40px;
      bottom: -40px;
      width: 180px;
      height: 180px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(209, 125, 32, 0.15) 0%, rgba(209, 125, 32, 0) 70%);
      pointer-events: none;
    }

    h1 {
      font-size: clamp(32px, 4.1vw, 56px);
      line-height: 0.94;
      margin: 0 0 10px;
      font-weight: 700;
      max-width: 11ch;
      font-family: Georgia, "Times New Roman", serif;
    }

    .hero p, .panel p {
      margin: 0;
      color: var(--muted);
      line-height: 1.55;
    }

    .stats {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-top: 18px;
    }

    .stat {
      padding: 14px;
      border-radius: 16px;
      background: linear-gradient(180deg, rgba(255, 253, 248, 0.98) 0%, rgba(245, 236, 222, 0.96) 100%);
      border: 1px solid var(--line);
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.75);
    }

    .stat strong {
      display: block;
      font-size: 24px;
      margin-bottom: 4px;
      font-family: Georgia, "Times New Roman", serif;
      overflow-wrap: anywhere;
    }

    .stat span {
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }

    .header-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 12px;
      flex-wrap: wrap;
    }

    .badge {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 999px;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      background: var(--accent-soft);
      color: var(--accent);
    }

    .panel-title {
      margin: 0;
      font-size: 22px;
      font-family: Georgia, "Times New Roman", serif;
    }

    .panel-subtitle {
      color: var(--muted);
      margin-top: 4px;
      font-size: 13px;
      line-height: 1.45;
    }

    .upload-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      margin-top: 14px;
    }

    .field {
      display: grid;
      gap: 8px;
    }

    .field label {
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }

    .field input {
      width: 100%;
      padding: 12px 14px;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(250, 246, 239, 0.94));
      color: var(--ink);
      font: inherit;
      transition: border-color 140ms ease, box-shadow 140ms ease, transform 140ms ease;
    }

    .field input:focus, .field textarea:focus {
      outline: none;
      border-color: rgba(25, 95, 82, 0.38);
      box-shadow: 0 0 0 4px rgba(25, 95, 82, 0.08);
    }

    .field input[type="file"] {
      padding: 10px;
      background: linear-gradient(180deg, #fffdf8 0%, #f4ebdd 100%);
    }

    .field input[type="file"]::file-selector-button {
      margin-right: 10px;
      padding: 9px 14px;
      border: 0;
      border-radius: 10px;
      background: linear-gradient(135deg, #214e43 0%, #2f8473 100%);
      color: #fff9f2;
      font: inherit;
      font-weight: 600;
      cursor: pointer;
      box-shadow: 0 8px 18px rgba(33, 78, 67, 0.18);
    }

    .actions {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-top: 14px;
    }

    .actions.single-action {
      grid-template-columns: 1fr;
    }

    .button {
      position: relative;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 48px;
      width: 100%;
      border: 1px solid transparent;
      border-radius: 999px;
      padding: 12px 18px;
      cursor: pointer;
      font: inherit;
      font-size: 14px;
      font-weight: 600;
      letter-spacing: 0.01em;
      transition: transform 140ms ease, opacity 140ms ease, box-shadow 140ms ease, border-color 140ms ease, filter 140ms ease;
      box-shadow: 0 10px 24px rgba(43, 49, 45, 0.06);
    }

    .button:hover {
      transform: translateY(-2px);
      box-shadow: 0 16px 32px rgba(43, 49, 45, 0.1);
      filter: saturate(1.02);
    }

    .button:disabled {
      opacity: 0.6;
      cursor: wait;
      transform: none;
      box-shadow: none;
    }

    .button-primary {
      background: linear-gradient(135deg, var(--accent) 0%, #2a7a69 100%);
      color: #fff7ef;
    }

    .button-secondary {
      background: linear-gradient(180deg, #fffaf2 0%, #efe2cf 100%);
      color: var(--ink);
      border-color: rgba(27, 42, 37, 0.1);
    }

    .button-secondary:hover {
      border-color: rgba(25, 95, 82, 0.22);
    }

    .button-neutral {
      background: linear-gradient(180deg, rgba(216, 235, 228, 0.76) 0%, rgba(191, 223, 213, 0.92) 100%);
      color: var(--accent-strong);
      border-color: rgba(25, 95, 82, 0.12);
    }

    .button-warning {
      background: linear-gradient(180deg, #fff5eb 0%, #f4e2c7 100%);
      color: #8a5a22;
      border-color: rgba(138, 90, 34, 0.14);
    }

    .import-panel {
      background:
        linear-gradient(180deg, rgba(255, 252, 246, 0.98) 0%, rgba(247, 239, 226, 0.94) 100%);
      box-shadow:
        inset 0 1px 0 rgba(255,255,255,0.7),
        0 18px 40px rgba(43, 49, 45, 0.05);
    }

    .action-note {
      margin-top: 8px;
      font-size: 12px;
      color: var(--muted);
      line-height: 1.45;
    }

    .status {
      min-height: 40px;
      color: var(--muted);
      font-size: 13px;
      margin-top: 12px;
      padding: 12px 14px;
      border-radius: 14px;
      background: rgba(255, 251, 245, 0.88);
      border: 1px solid var(--line);
      line-height: 1.5;
    }

    .status.error {
      color: var(--critical);
      background: rgba(178, 75, 69, 0.08);
      border-color: rgba(178, 75, 69, 0.18);
    }

    .status.success {
      color: var(--accent);
      background: rgba(25, 95, 82, 0.08);
      border-color: rgba(25, 95, 82, 0.16);
    }

    .preview-panel {
      margin-top: 12px;
      padding: 14px;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: rgba(255, 253, 248, 0.88);
    }

    .preview-panel.hidden {
      display: none;
    }

    .preview-title {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      margin-bottom: 8px;
      flex-wrap: wrap;
    }

    .preview-list {
      display: grid;
      gap: 8px;
      margin: 0;
      padding: 0;
      list-style: none;
    }

    .preview-item {
      display: grid;
      grid-template-columns: 130px 1fr auto;
      gap: 10px;
      align-items: center;
      padding: 10px 12px;
      border-radius: 12px;
      background: #fffaf2;
      border: 1px solid var(--line);
    }

    .preview-item code {
      color: var(--accent);
      font-size: 13px;
    }

    .mini-badge {
      display: inline-flex;
      padding: 5px 8px;
      border-radius: 999px;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      background: #efe5d6;
      color: var(--ink);
    }

    .mini-badge.openai {
      background: rgba(31, 111, 95, 0.12);
      color: var(--accent);
    }

    .question-panel {
      margin-top: 12px;
      padding: 14px;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: rgba(255, 251, 244, 0.9);
    }

    .question-panel.hidden {
      display: none;
    }

    .question-grid {
      display: grid;
      gap: 10px;
      margin-top: 10px;
    }

    .check-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 6px;
    }

    .field textarea {
      width: 100%;
      min-height: 88px;
      padding: 12px 14px;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.92);
      color: var(--ink);
      font: inherit;
      resize: vertical;
    }

    .check-grid label {
      display: flex;
      gap: 8px;
      align-items: center;
      font-size: 14px;
      color: var(--ink);
      padding: 8px 10px;
      border-radius: 12px;
      background: rgba(255, 255, 255, 0.78);
      border: 1px solid rgba(27, 42, 37, 0.06);
    }

    .chart-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }

    .helper-note {
      margin-top: 10px;
      padding: 14px 15px;
      border-radius: 16px;
      background: linear-gradient(180deg, rgba(223, 241, 235, 0.88), rgba(213, 235, 228, 0.7));
      border: 1px solid rgba(25, 95, 82, 0.14);
      color: var(--accent-strong);
      font-size: 13px;
      line-height: 1.5;
    }

    .chart-card {
      padding: 14px;
      border-radius: 16px;
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.72), rgba(245, 236, 222, 0.88));
      border: 1px solid rgba(27, 42, 37, 0.08);
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.7);
    }

    .chart-card h3 {
      margin: 0 0 4px;
      font-size: 16px;
      font-family: Georgia, "Times New Roman", serif;
    }

    .chart-card p {
      margin: 0 0 12px;
      font-size: 12px;
      color: var(--muted);
    }

    .priority-chart {
      display: grid;
      gap: 10px;
    }

    .priority-row {
      display: grid;
      grid-template-columns: 90px 1fr 36px;
      gap: 10px;
      align-items: center;
    }

    .priority-label {
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
    }

    .priority-bar-track {
      position: relative;
      height: 10px;
      border-radius: 999px;
      overflow: hidden;
      background: rgba(27, 42, 37, 0.08);
    }

    .priority-bar {
      height: 100%;
      border-radius: inherit;
      background: var(--accent);
      transition: width 220ms ease;
    }

    .priority-bar.critical { background: linear-gradient(90deg, #cf6e68 0%, var(--critical) 100%); }
    .priority-bar.high { background: linear-gradient(90deg, #efbb77 0%, var(--high) 100%); }
    .priority-bar.medium { background: linear-gradient(90deg, #8fbaef 0%, var(--medium) 100%); }
    .priority-bar.low { background: linear-gradient(90deg, #9cc49b 0%, var(--low) 100%); }

    .priority-value {
      font-weight: 700;
      text-align: right;
      font-family: Georgia, "Times New Roman", serif;
    }

    .timeline {
      display: grid;
      gap: 10px;
    }

    .timeline-item {
      display: grid;
      grid-template-columns: 64px 1fr;
      gap: 10px;
      align-items: start;
    }

    .timeline-date {
      padding: 10px 8px;
      border-radius: 14px;
      background: #f4ebdd;
      border: 1px solid rgba(27, 42, 37, 0.08);
      text-align: center;
    }

    .timeline-date strong {
      display: block;
      font-size: 16px;
      font-family: Georgia, "Times New Roman", serif;
    }

    .timeline-date span {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
    }

    .timeline-body {
      padding: 10px 12px;
      border-radius: 14px;
      background: rgba(255, 255, 255, 0.78);
      border: 1px solid rgba(27, 42, 37, 0.08);
    }

    .timeline-body strong {
      display: block;
      margin-bottom: 4px;
      font-size: 14px;
    }

    .timeline-body span {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }

    .board-shell {
      overflow: hidden;
      background: linear-gradient(180deg, rgba(255, 252, 246, 0.94), rgba(248, 241, 229, 0.9));
    }

    .board-toolbar {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
      margin-bottom: 12px;
      flex-wrap: wrap;
    }

    .board-toolbar h2 {
      margin: 0;
      font-size: 24px;
      font-family: Georgia, "Times New Roman", serif;
    }

    .board-toolbar p {
      margin-top: 6px;
      max-width: 760px;
    }

    .table-guide {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }

    .guide-pill {
      padding: 7px 10px;
      border-radius: 999px;
      background: rgba(242, 232, 217, 0.86);
      border: 1px solid rgba(27, 42, 37, 0.08);
      color: var(--muted);
      font-size: 11px;
    }

    .table-shell {
      overflow-x: auto;
      border-radius: 16px;
      border: 1px solid rgba(27, 42, 37, 0.08);
      background: rgba(255, 255, 255, 0.56);
    }

    table {
      width: 100%;
      border-collapse: collapse;
    }

    th, td {
      padding: 12px 10px;
      text-align: left;
      vertical-align: top;
      border-top: 1px solid var(--line);
    }

    th {
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
      background: rgba(244, 235, 223, 0.72);
    }

    tbody tr {
      transition: transform 140ms ease, background 140ms ease;
    }

    tbody tr:hover {
      background: rgba(31, 111, 95, 0.04);
      transform: translateY(-1px);
    }

    .priority {
      display: inline-flex;
      border-radius: 999px;
      padding: 5px 8px;
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }

    .priority.critical { background: rgba(179, 58, 58, 0.12); color: var(--critical); }
    .priority.high { background: rgba(217, 119, 6, 0.12); color: var(--high); }
    .priority.medium { background: rgba(59, 130, 246, 0.12); color: var(--medium); }
    .priority.low { background: rgba(75, 127, 82, 0.12); color: var(--low); }

    .origin-badge {
      display: inline-flex;
      align-items: center;
      margin-top: 8px;
      padding: 5px 8px;
      border-radius: 999px;
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      background: rgba(242, 232, 217, 0.95);
      color: var(--muted);
      border: 1px solid rgba(27, 42, 37, 0.08);
    }

    .origin-badge.ai {
      background: rgba(62, 121, 201, 0.14);
      color: var(--medium);
    }

    .explanation {
      min-width: 240px;
      color: var(--muted);
      line-height: 1.4;
      font-size: 13px;
    }

    .empty {
      padding: 24px;
      border-radius: 18px;
      background: linear-gradient(180deg, #fffcf6 0%, #f5ecdc 100%);
      border: 1px dashed var(--line);
      color: var(--muted);
    }

    .empty strong {
      display: block;
      margin-bottom: 8px;
      color: var(--ink);
      font-size: 18px;
    }

    @media (max-width: 900px) {
      .workspace { grid-template-columns: 1fr; }
      .sidebar { position: static; }
      .workflow-strip { grid-template-columns: 1fr; }
      .hero-board { grid-template-columns: 1fr; }
      .chart-grid { grid-template-columns: 1fr; }
      .upload-grid { grid-template-columns: 1fr; }
      .actions { grid-template-columns: 1fr; }
      .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .app-shell { width: min(100% - 12px, 1380px); margin: 6px auto; padding: 10px; }
      .topbar { padding: 12px; }
      .brand { flex-direction: column; align-items: flex-start; }
      .brand-logo { width: min(320px, 100%); }
      .topbar-meta { justify-content: flex-start; }
      th:nth-child(3), td:nth-child(3),
      th:nth-child(4), td:nth-child(4) { display: none; }
    }
  </style>
</head>
<body>
  <main class="app-shell">
    <header class="topbar">
      <div class="brand">
        <img class="brand-logo" src="/static/air-logo.png" alt="AIR logo" />
        <div class="brand-copy">
          <strong>Artificial Intelligence Replenisher</strong>
          <span>Inventory planning workspace for smarter replenishment decisions</span>
        </div>
      </div>
      <div class="topbar-meta">
        <span class="meta-pill live">Planner workspace</span>
        <span class="meta-pill">CSV + Excel imports</span>
        <span class="meta-pill">Demand trend aware</span>
      </div>
    </header>

    <section class="workspace">
      <aside class="sidebar">
        <section class="sidebar-section">
          <p class="eyebrow">Artificial Intelligence Replenisher</p>
          <h1 class="sidebar-title">AIR keeps your inventory decisions ahead of the stockout.</h1>
          <p class="sidebar-copy">
            Feed AIR your inventory and optional daily usage history, then let it map columns,
            refine demand, ask the last planner questions, and produce a clean verdict table.
          </p>
        </section>
        <section class="sidebar-section">
          <p class="eyebrow">What AIR Watches</p>
          <div class="signal-grid">
            <div class="signal-card">
              <strong>Inventory signals</strong>
              <span>On hand, on order, lead time, minimum order rules, and safety coverage.</span>
            </div>
            <div class="signal-card">
              <strong>Demand behavior</strong>
              <span>Weekly labels, usage uploads, and changing movement patterns across items.</span>
            </div>
            <div class="signal-card">
              <strong>Planner context</strong>
              <span>Delivery days, spoilage windows, claims, and operating notes before verdict.</span>
            </div>
          </div>
        </section>
      </aside>

      <div class="content-stack">
        <section class="workflow-strip">
          <article class="workflow-card">
            <div class="workflow-step">1</div>
            <strong>Upload your files</strong>
              <span>Start with inventory, then add daily usage or a weekly LW/CW sales report to improve trend accuracy.</span>
          </article>
          <article class="workflow-card">
            <div class="workflow-step">2</div>
            <strong>Review AIR's understanding</strong>
            <span>Preview the mapped columns and answer the planner questions AIR still needs.</span>
          </article>
          <article class="workflow-card">
            <div class="workflow-step">3</div>
            <strong>Read and export the verdict</strong>
            <span>Use the live recommendation board, then export the final Excel workbook when ready.</span>
          </article>
        </section>

        <section class="hero-board">
          <article class="panel hero-card">
            <div class="header-row">
              <div>
                <p class="eyebrow">Operations Snapshot</p>
                <h2 class="panel-title">Decision board</h2>
                <p class="panel-subtitle">Live counts update as AIR ingests files, learns assumptions, and ranks urgency.</p>
              </div>
              <span class="badge">Live planning state</span>
            </div>
            <div class="stats" id="stats">
              <div class="stat"><strong id="stat-total">0</strong><span>Total items</span></div>
              <div class="stat"><strong id="stat-reorder">0</strong><span>Need reorder</span></div>
              <div class="stat"><strong id="stat-critical">0</strong><span>Critical items</span></div>
              <div class="stat"><strong id="stat-source">None</strong><span>Latest data source</span></div>
            </div>
            <div class="table-guide" style="margin-top: 16px;">
              <span class="guide-pill" id="verdict-origin-pill">Verdict source: AIR analysis</span>
              <span class="guide-pill" id="inventory-origin-pill">Inventory mapping: not loaded</span>
              <span class="guide-pill" id="usage-origin-pill">Usage mapping: not loaded</span>
            </div>
          </article>

          <aside class="panel import-panel">
            <div class="header-row">
              <div>
                <p class="eyebrow">Import Center</p>
                <h2 class="panel-title" style="font-size: 26px;">Import Data</h2>
                <p class="panel-subtitle">
                  Upload your inventory file, then optionally upload daily usage history so AIR can refine demand trends before recommending what to order.
                </p>
              </div>
              <span class="badge">API-driven dashboard</span>
            </div>
            <div class="upload-grid">
              <div class="field">
                <label for="inventory-source">Inventory source</label>
                <input id="inventory-source" name="inventory-source" type="text" value="inventory-upload" placeholder="weekly-inventory" />
              </div>
              <div class="field">
                <label for="inventory-file">Inventory file</label>
                <input id="inventory-file" name="inventory-file" type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" />
              </div>
              <div class="field">
                <label for="usage-source">Daily usage source</label>
                <input id="usage-source" name="usage-source" type="text" value="usage-upload" placeholder="daily-usage-history" />
              </div>
              <div class="field">
                <label for="usage-file">Daily usage file</label>
                <input id="usage-file" name="usage-file" type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" />
              </div>
            </div>
            <div class="actions">
              <button id="inventory-preview" class="button button-secondary">Preview Inventory Columns</button>
              <button id="inventory-upload" class="button button-primary">Analyze Inventory</button>
              <button id="usage-preview" class="button button-secondary">Preview Usage Columns</button>
              <button id="usage-upload" class="button button-neutral">Analyze Usage Trends</button>
              <button id="refresh" class="button button-secondary">Refresh Recommendation Board</button>
              <button id="clear-memory" class="button button-warning" type="button">Reset Saved Answers</button>
              <button id="export-verdict" class="button button-secondary" disabled>Export Verdict Workbook</button>
            </div>
            <div class="action-note">Start with inventory, add usage if you have it, then export once the verdict table is ready.</div>
            <div class="helper-note">
              Best order to use AIR: import your inventory file first, preview the mapping if the spreadsheet is unusual, then optionally import daily usage or a weekly LW/CW sales report for stronger demand analysis.
            </div>
            <p class="status" id="status">Waiting for files. AIR can infer inventory columns like SKU, on hand, on order, and weekly sales labels. If an OpenAI API key is configured, AIR can also use semantic header understanding for messier spreadsheets. Daily usage can be transaction data with a SKU, date, and units, or a weekly sales report with columns like LW Mon through CW Sat.</p>
            <section id="preview-panel" class="preview-panel hidden">
              <div class="preview-title">
                <strong id="preview-heading">Mapping preview</strong>
                <span id="preview-ai-badge" class="mini-badge">Heuristic</span>
              </div>
              <ul id="preview-list" class="preview-list"></ul>
            </section>
            <section id="question-panel" class="question-panel hidden">
              <div class="preview-title">
                <strong>Questions Before Final Verdict</strong>
                <span class="mini-badge">Required</span>
              </div>
              <p style="color: var(--muted); margin: 0;">AIR needs your operating answers before it gives the final recommendation set. It will also ask if there is anything else you would like it to know, like spoiled arrivals or frequent claims.</p>
              <form id="question-form" class="question-grid"></form>
              <div class="actions single-action">
                <button id="save-assumptions" class="button button-primary" type="button">Save Answers</button>
              </div>
            </section>
          </aside>
        </section>

        <section class="chart-grid">
          <article class="chart-card">
            <h3>Reorder Pressure</h3>
            <p>How many current recommendations fall into each urgency band.</p>
            <div id="priority-chart" class="priority-chart"></div>
          </article>
          <article class="chart-card">
            <h3>Upcoming Stockouts</h3>
            <p>The nearest projected stockout dates so planners can see what needs attention first.</p>
            <div id="stockout-timeline" class="timeline"></div>
          </article>
        </section>

        <section class="panel board-shell">
          <div class="board-toolbar">
            <div>
              <p class="eyebrow">Recommendation Board</p>
              <h2>Recommendations</h2>
              <p>Final verdicts appear here after AIR has your inventory data and any required planning answers. The highest-risk items rise to the top of your attention.</p>
            </div>
            <span class="badge">Live analysis output</span>
          </div>
          <div class="table-guide">
            <span class="guide-pill">Read `Priority` to see urgency</span>
            <span class="guide-pill">Check `Stockout Date` to see timing risk</span>
            <span class="guide-pill">Use `Explanation` to understand why AIR suggested the order</span>
          </div>
          <div id="content" class="empty">Loading AIR recommendations...</div>
        </section>
      </div>
    </section>
  </main>

  <script>
    const content = document.getElementById("content");
    const refresh = document.getElementById("refresh");
    const inventoryPreview = document.getElementById("inventory-preview");
    const inventoryUpload = document.getElementById("inventory-upload");
    const usagePreview = document.getElementById("usage-preview");
    const usageUpload = document.getElementById("usage-upload");
    const exportVerdict = document.getElementById("export-verdict");
    const inventoryFileInput = document.getElementById("inventory-file");
    const usageFileInput = document.getElementById("usage-file");
    const inventorySourceInput = document.getElementById("inventory-source");
    const usageSourceInput = document.getElementById("usage-source");
    const status = document.getElementById("status");
    const previewPanel = document.getElementById("preview-panel");
    const previewHeading = document.getElementById("preview-heading");
    const previewAiBadge = document.getElementById("preview-ai-badge");
    const previewList = document.getElementById("preview-list");
    const questionPanel = document.getElementById("question-panel");
    const questionForm = document.getElementById("question-form");
    const saveAssumptionsButton = document.getElementById("save-assumptions");
    const clearMemoryButton = document.getElementById("clear-memory");
    const priorityChart = document.getElementById("priority-chart");
    const stockoutTimeline = document.getElementById("stockout-timeline");
    const verdictOriginPill = document.getElementById("verdict-origin-pill");
    const inventoryOriginPill = document.getElementById("inventory-origin-pill");
    const usageOriginPill = document.getElementById("usage-origin-pill");

    let currentAssumptions = {
      shipping_days_per_week: null,
      arrival_days: [],
      default_spoilage_days: null,
      produce_spoilage_days: null,
      herb_spoilage_days: null
    };

    function setStatus(message, kind = "") {
      status.textContent = message;
      status.className = kind ? `status ${kind}` : "status";
    }

    function renderQuestions(payload) {
      currentAssumptions = payload.assumptions;
      if (!payload.questions.length) {
        questionPanel.classList.add("hidden");
        return;
      }

      questionPanel.classList.remove("hidden");
      questionForm.innerHTML = payload.questions.map((question) => {
        if (question.input_type === "multiselect") {
          const selected = new Set(currentAssumptions[question.id] || []);
          const options = question.options.map((option) => `
            <label>
              <input type="checkbox" name="${question.id}" value="${option}" ${selected.has(option) ? "checked" : ""} />
              <span>${option}</span>
            </label>
          `).join("");
          return `
            <div class="field">
              <label>${question.prompt}</label>
              <p style="margin: 0; color: var(--muted); font-size: 14px;">${question.help_text}</p>
              <div class="check-grid">${options}</div>
            </div>
          `;
        }

        const value = currentAssumptions[question.id] ?? "";
        return `
          <div class="field">
            <label for="${question.id}">${question.prompt}</label>
            <p style="margin: 0; color: var(--muted); font-size: 14px;">${question.help_text}</p>
            ${question.input_type === "textarea"
              ? `<textarea id="${question.id}" name="${question.id}" placeholder="Optional notes about claims, spoilage, substitutions, or delivery issues.">${value}</textarea>`
              : `<input id="${question.id}" name="${question.id}" type="number" min="0" value="${value}" />`}
          </div>
        `;
      }).join("");
    }

    function renderPreview(data) {
      previewPanel.classList.remove("hidden");
      previewHeading.textContent = `${data.dataset_kind === "usage" ? "Usage" : "Inventory"} mapping preview`;
      previewAiBadge.textContent = data.ai_enabled ? "OpenAI ready" : "Heuristic only";
      previewAiBadge.className = data.ai_enabled ? "mini-badge openai" : "mini-badge";
      previewList.innerHTML = data.mappings.map((mapping) => `
        <li class="preview-item">
          <strong>${mapping.field}</strong>
          <code>${mapping.header || "Not mapped"}</code>
          <span class="mini-badge ${mapping.source === "openai" ? "openai" : ""}">${mapping.source}</span>
        </li>
      `).join("");
    }

    function setStats(data) {
      const recommendations = data.recommendations || [];
      document.getElementById("stat-total").textContent = String(data.total_records ?? 0);
      document.getElementById("stat-reorder").textContent = String(recommendations.filter(item => item.needs_reorder).length);
      document.getElementById("stat-critical").textContent = String(recommendations.filter(item => item.priority === "critical").length);
      document.getElementById("stat-source").textContent = data.source || "None";
      exportVerdict.disabled = !data.final_verdict_ready || !recommendations.length;
      setOriginPills(data);
    }

    function setOriginPills(data) {
      verdictOriginPill.textContent = `Verdict source: ${data.verdict_origin === "ai-assisted analysis" ? "AI-assisted analysis" : "AIR analysis"}`;
      inventoryOriginPill.textContent = `Inventory mapping: ${formatOrigin(data.inventory_mapping_origin, "not loaded")}`;
      usageOriginPill.textContent = `Usage mapping: ${formatOrigin(data.usage_mapping_origin, "not loaded")}`;
    }

    function formatOrigin(origin, fallback) {
      if (!origin || origin === "none") {
        return fallback;
      }
      if (origin === "openai") {
        return "AI-assisted";
      }
      return "rules-based";
    }

    function renderTable(data) {
      const recommendations = data.recommendations || [];
      if (!data.final_verdict_ready) {
        content.className = "empty";
        content.innerHTML = "<strong>Waiting for planner answers</strong>AIR is waiting for your operating answers before it gives the final verdict.";
        exportVerdict.disabled = true;
        renderCharts([]);
        renderQuestions({ assumptions: currentAssumptions, questions: data.pending_questions || [] });
        return;
      }
      if (!recommendations.length) {
        content.className = "empty";
        content.innerHTML = "<strong>No recommendations yet</strong>Feed AIR a dataset to see replenishment recommendations here.";
        exportVerdict.disabled = true;
        renderCharts([]);
        return;
      }

      content.className = "";
      renderCharts(recommendations);
      const rows = recommendations.map((item) => `
        <tr>
          <td>
            <strong>${item.sku}</strong><br>
            <span style="color: var(--muted);">${item.name}</span><br>
            <span class="origin-badge ${item.ai_refined ? "ai" : ""}">${item.ai_refined ? "AI refined" : "Rules baseline"}</span>
          </td>
          <td>${item.current_stock}</td>
          <td>${item.reorder_point}</td>
          <td>${item.target_stock}</td>
          <td>${item.recommended_order_qty}</td>
          <td><span class="priority ${item.priority}">${item.priority}</span></td>
          <td>${item.projected_stockout_date || "Stable"}</td>
          <td class="explanation">${item.explanation}</td>
        </tr>
      `).join("");

      content.innerHTML = `
        <div class="table-shell">
          <table>
            <thead>
              <tr>
                <th>Item</th>
                <th>Stock</th>
                <th>Reorder Point</th>
                <th>Target</th>
                <th>Suggested Order</th>
                <th>Priority</th>
                <th>Stockout Date</th>
                <th>Explanation</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      `;
    }

    function renderCharts(recommendations) {
      renderPriorityChart(recommendations);
      renderStockoutTimeline(recommendations);
    }

    function renderPriorityChart(recommendations) {
      const counts = {
        critical: recommendations.filter(item => item.priority === "critical").length,
        high: recommendations.filter(item => item.priority === "high").length,
        medium: recommendations.filter(item => item.priority === "medium").length,
        low: recommendations.filter(item => item.priority === "low").length
      };
      const maxValue = Math.max(1, counts.critical, counts.high, counts.medium, counts.low);
      const order = ["critical", "high", "medium", "low"];

      priorityChart.innerHTML = order.map((priority) => {
        const count = counts[priority];
        const width = Math.max(count > 0 ? 12 : 0, Math.round((count / maxValue) * 100));
        return `
          <div class="priority-row">
            <span class="priority-label">${priority}</span>
            <div class="priority-bar-track">
              <div class="priority-bar ${priority}" style="width: ${width}%;"></div>
            </div>
            <span class="priority-value">${count}</span>
          </div>
        `;
      }).join("");
    }

    function renderStockoutTimeline(recommendations) {
      const items = recommendations
        .filter(item => item.projected_stockout_date)
        .sort((left, right) => left.projected_stockout_date.localeCompare(right.projected_stockout_date))
        .slice(0, 5);

      if (!items.length) {
        stockoutTimeline.innerHTML = `
          <div class="timeline-body">
            <strong>No immediate stockout signals</strong>
            <span>AIR will surface the earliest projected stockouts here once items are trending toward reorder risk.</span>
          </div>
        `;
        return;
      }

      stockoutTimeline.innerHTML = items.map((item) => {
        const dateValue = new Date(item.projected_stockout_date + "T00:00:00");
        const day = String(dateValue.getDate()).padStart(2, "0");
        const month = dateValue.toLocaleString("en-US", { month: "short" }).toUpperCase();
        const daysText = item.days_until_stockout === null
          ? "Stable horizon"
          : `${item.days_until_stockout} day(s) remaining`;

        return `
          <div class="timeline-item">
            <div class="timeline-date">
              <strong>${day}</strong>
              <span>${month}</span>
            </div>
            <div class="timeline-body">
              <strong>${item.sku} · ${item.name}</strong>
              <span>${daysText}. Priority is ${item.priority} with a suggested order of ${item.recommended_order_qty}.</span>
            </div>
          </div>
        `;
      }).join("");
    }

    async function loadRecommendations() {
      content.className = "empty";
      content.textContent = "Loading AIR recommendations...";
      try {
        await loadQuestions();
        const response = await fetch("/recommendations");
        const data = await response.json();
        setStats(data);
        renderTable(data);
      } catch (error) {
        content.className = "empty";
        content.textContent = "AIR could not load recommendations right now.";
        setStatus("AIR could not load recommendations right now.", "error");
      }
    }

    async function loadQuestions() {
      const response = await fetch("/analysis/questions");
      const data = await response.json();
      renderQuestions(data);
      return data;
    }

    async function uploadDataset(file, source, endpoint, button, successLabel) {
      if (!file) {
        setStatus("Choose a file before importing.", "error");
        return;
      }

      const formData = new FormData();
      formData.append("source", source);
      formData.append("file", file);

      button.disabled = true;
      setStatus(`Importing ${file.name} into AIR...`);

      try {
        const response = await fetch(endpoint, {
          method: "POST",
          body: formData
        });

        const data = await response.json();
        if (!response.ok) {
          const detail = typeof data.detail === "string" ? data.detail : "Upload failed.";
          throw new Error(detail);
        }

        setStatus(`${successLabel} AIR ingested ${data.ingested_records} record(s) from ${data.source}.`, "success");
        await loadRecommendations();
      } catch (error) {
        setStatus(error.message || "Upload failed.", "error");
      } finally {
        button.disabled = false;
      }
    }

    async function previewDataset(file, endpoint, button, label) {
      if (!file) {
        setStatus("Choose a file before previewing.", "error");
        return;
      }

      const formData = new FormData();
      formData.append("file", file);

      button.disabled = true;
      setStatus(`Previewing ${file.name} with AIR...`);

      try {
        const response = await fetch(endpoint, {
          method: "POST",
          body: formData
        });
        const data = await response.json();
        if (!response.ok) {
          const detail = typeof data.detail === "string" ? data.detail : "Preview failed.";
          throw new Error(detail);
        }
        renderPreview(data);
        setStatus(`${label} preview is ready. Review the mapped columns below.`, "success");
      } catch (error) {
        setStatus(error.message || "Preview failed.", "error");
      } finally {
        button.disabled = false;
      }
    }

    async function saveAssumptions() {
      const formData = new FormData(questionForm);
      const payload = {
        shipping_days_per_week: _readOptionalNumber(formData.get("shipping_days_per_week")),
        arrival_days: formData.getAll("arrival_days"),
        default_spoilage_days: _readOptionalNumber(formData.get("default_spoilage_days")),
        produce_spoilage_days: _readOptionalNumber(formData.get("produce_spoilage_days")),
        herb_spoilage_days: _readOptionalNumber(formData.get("herb_spoilage_days")),
        additional_notes: String(formData.get("additional_notes") || "").trim()
      };

      saveAssumptionsButton.disabled = true;
      setStatus("Saving your operating answers into AIR...");
      try {
        const response = await fetch("/analysis/assumptions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) {
          const detail = typeof data.detail === "string" ? data.detail : "Could not save answers.";
          throw new Error(detail);
        }
        renderQuestions(data);
        setStatus(data.final_verdict_ready ? "Answers saved. AIR can now give the final verdict." : "Answers saved. AIR still needs a few more details.", "success");
        await loadRecommendations();
      } catch (error) {
        setStatus(error.message || "Could not save answers.", "error");
      } finally {
        saveAssumptionsButton.disabled = false;
      }
    }

    async function clearSavedMemory() {
      const confirmed = window.confirm("Clear AIR's saved shipping, arrival, spoilage, and notes memory for this dataset?");
      if (!confirmed) {
        return;
      }

      clearMemoryButton.disabled = true;
      setStatus("Clearing AIR's saved planner memory...");
      try {
        const response = await fetch("/analysis/assumptions/reset", {
          method: "POST"
        });
        const data = await response.json();
        if (!response.ok) {
          const detail = typeof data.detail === "string" ? data.detail : "Could not clear saved memory.";
          throw new Error(detail);
        }
        renderQuestions(data);
        setStatus("Saved planner memory cleared. AIR is ready for updated arrival, shipping, and spoilage answers.", "success");
        await loadRecommendations();
      } catch (error) {
        setStatus(error.message || "Could not clear saved memory.", "error");
      } finally {
        clearMemoryButton.disabled = false;
      }
    }

    function _readOptionalNumber(value) {
      if (value === null || value === "") {
        return null;
      }
      return Number(value);
    }

    function exportRecommendations() {
      window.location.href = "/recommendations/export";
    }

    inventoryPreview.addEventListener("click", () => {
      previewDataset(inventoryFileInput.files[0], "/datasets/preview-file", inventoryPreview, "Inventory");
    });
    inventoryUpload.addEventListener("click", () => {
      const file = inventoryFileInput.files[0];
      const source = inventorySourceInput.value.trim() || "inventory-upload";
      uploadDataset(file, source, "/datasets/ingest-file", inventoryUpload, "Inventory imported.");
    });

    usagePreview.addEventListener("click", () => {
      previewDataset(usageFileInput.files[0], "/datasets/preview-usage-file", usagePreview, "Usage");
    });
    usageUpload.addEventListener("click", () => {
      const file = usageFileInput.files[0];
      const source = usageSourceInput.value.trim() || "usage-upload";
      uploadDataset(file, source, "/datasets/ingest-usage-file", usageUpload, "Daily usage imported.");
    });

    exportVerdict.addEventListener("click", exportRecommendations);
    saveAssumptionsButton.addEventListener("click", saveAssumptions);
    clearMemoryButton.addEventListener("click", clearSavedMemory);
    refresh.addEventListener("click", loadRecommendations);
    loadRecommendations();
  </script>
</body>
</html>
"""
