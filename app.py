"""app.py — FastAPI HTTP API for kr-forensic-finance.

Start with:
    krff serve              # http://127.0.0.1:8000
    krff serve --reload     # dev mode with auto-reload
    uvicorn app:app --reload

Endpoints:
    GET /api/status                             → PipelineStatus
    GET /api/quality                            → DataQuality
    GET /api/companies/{corp_code}/summary      → CompanySummary
    GET /api/companies/{corp_code}/report       → HTML
    GET /api/alerts                             → AlertList
    GET /api/monitor/status                     → MonitorStatus
    GET /docs                                   → Swagger UI (auto-generated)
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from src.models import AlertList, CompanySummary, DataQuality, MonitorStatus, PipelineStatus
from src.quality import get_quality
from src.report import get_company_summary, get_report_html
from src.status import get_status

app = FastAPI(
    title="kr-forensic-finance",
    description="Anomaly screening API for Korean listed companies",
    version="1.5.0",
)


def _validate_corp_code(corp_code: str) -> str:
    """Validate and zero-pad corp_code; raise 422 if not 1–8 digits."""
    if not corp_code.isdigit() or not (1 <= len(corp_code) <= 8):
        raise HTTPException(status_code=422, detail="corp_code must be 1–8 digits")
    return corp_code.zfill(8)


@app.get("/api/companies/{corp_code}/summary", response_model=CompanySummary)
def company_summary(corp_code: str):
    """Return structured forensic summary for one company."""
    corp_code = _validate_corp_code(corp_code)
    return get_company_summary(corp_code)


@app.get("/api/companies/{corp_code}/report", response_class=HTMLResponse)
def company_report(corp_code: str, skip_claude: bool = True):
    """Return full HTML forensic report for one company."""
    corp_code = _validate_corp_code(corp_code)
    return get_report_html(corp_code, skip_claude=skip_claude)


@app.get("/api/status", response_model=PipelineStatus)
def pipeline_status():
    """Return pipeline artifact inventory (which parquets exist, row counts)."""
    return get_status()


@app.get("/api/quality", response_model=DataQuality)
def data_quality():
    """Return data quality metrics (null rates, coverage gaps, stat test outputs)."""
    return get_quality()


@app.get("/api/alerts", response_model=AlertList)
def list_alerts():
    """Return recent alerts from the monitoring system (Phase 3 stub)."""
    return {"alerts": [], "total": 0}


@app.get("/api/monitor/status", response_model=MonitorStatus)
def monitor_status():
    """Return monitoring system status (Phase 3 stub)."""
    return {"running": False, "sources": []}
