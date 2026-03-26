"""src/ — Core package for kr-forensic-finance.

Public API:
    src.models       — Pydantic models (CompanySummary, PipelineStatus, DataQuality)
    src.data_access  — Data loading (load_parquet, load_csv, load_company_name)
    src.report       — get_company_summary, get_report_html, generate_report
    src.status       — get_status, format_status
    src.quality      — get_quality, format_quality
    src.constants    — BENEISH_THRESHOLD, flag names, scoring thresholds
"""
