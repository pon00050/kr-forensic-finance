"""src/models.py — Pydantic models for API response shapes.

These models document the contracts of dict-returning functions in src/.
They are NOT used to construct return values today — functions still return
plain dicts. A future FastAPI layer will call .model_validate() at the
serialization boundary.

Usage:
    from src.models import CompanySummary, PipelineStatus, DataQuality
    CompanySummary.model_validate(get_company_summary("01051092"))
"""

from __future__ import annotations

from pydantic import BaseModel


# ─── CompanySummary (matches build_company_summary output) ────────────────────

class BeneishYearScore(BaseModel):
    year: int
    m_score: float
    risk_tier: str
    flag: bool


class CompanySummary(BaseModel):
    corp_code: str
    company_name: str
    ticker: str
    beneish_years: list[BeneishYearScore]
    cb_bw_count: int
    cb_bw_flagged_count: int
    cb_bw_max_flags: int
    cb_bw_flag_types: list[str]
    timing_anomaly_count: int
    timing_flagged_count: int
    officer_network_centrality: float | None
    officer_network_appears_in_multiple: bool


# ─── ClaudeFlag (matches synthesize_with_claude output items) ─────────────────

class ClaudeFlag(BaseModel):
    source_quote: str
    flag_type: str
    severity: str  # "low" | "medium" | "high"


# ─── PipelineStatus (matches get_status output) ──────────────────────────────

class ArtifactStatus(BaseModel):
    name: str
    description: str
    exists: bool
    rows: int | None
    cols: int | None
    size_bytes: int | None
    modified: str | None


class RunSummary(BaseModel):
    total_companies: int
    full_data: int
    partial_data: int
    no_data: int
    errors: int
    completed_at: str | None
    last_modified: str


class PipelineStatusSummary(BaseModel):
    present: int
    total: int


class PipelineStatus(BaseModel):
    artifacts: list[ArtifactStatus]
    summary: PipelineStatusSummary
    run_summary: RunSummary | None


# ─── DataQuality (matches get_quality output) ────────────────────────────────

class TableQuality(BaseModel):
    name: str
    rows: int
    cols: int
    null_count: int
    null_pct: float
    inf_count: int
    issues: str
    col_nulls: dict[str, tuple[int, float]]
    modified: str


class StatOutputStatus(BaseModel):
    name: str
    exists: bool
    rows: int | None
    modified: str | None
    blocked_reason: str | None


class QualitySummary(BaseModel):
    tables_with_issues: int
    missing_outputs: int
    blocked_outputs: int


class DataQuality(BaseModel):
    tables: list[TableQuality]
    coverage: dict[str, str]
    stat_outputs: list[StatOutputStatus]
    summary: QualitySummary



# ─── Phase 3 — Monitoring models ────────────────────────────────────────────

class AlertEvent(BaseModel):
    alert_id: str
    created_at: str
    source: str            # "dart_rss" | "krx_warning" | "fsc_enforcement"
    corp_code: str
    corp_name: str
    event_type: str
    event_date: str
    severity: str          # "info" | "low" | "medium" | "high" | "critical"
    action_taken: str
    resolved: bool


class MonitorStatus(BaseModel):
    running: bool
    sources: list[str]


class AlertList(BaseModel):
    alerts: list[AlertEvent]
    total: int


__all__ = [
    "BeneishYearScore",
    "CompanySummary",
    "ClaudeFlag",
    "ArtifactStatus",
    "RunSummary",
    "PipelineStatusSummary",
    "PipelineStatus",
    "TableQuality",
    "StatOutputStatus",
    "QualitySummary",
    "DataQuality",
    "AlertEvent",
    "MonitorStatus",
    "AlertList",
]
