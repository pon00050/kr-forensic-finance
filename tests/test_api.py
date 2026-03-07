"""Tests for the FastAPI HTTP API (app.py)."""

import pytest
from fastapi.testclient import TestClient

import app as app_module
from app import app

client = TestClient(app)


def _minimal_status() -> dict:
    return {
        "artifacts": [],
        "summary": {"present": 0, "total": 11},
        "run_summary": None,
    }


def _minimal_quality() -> dict:
    return {
        "tables": [],
        "coverage": {
            "isin": "unavailable",
            "disclosures": "unavailable",
            "price": "unavailable",
        },
        "stat_outputs": [],
        "summary": {
            "tables_with_issues": 0,
            "missing_outputs": 0,
            "blocked_outputs": 0,
        },
    }


def _minimal_summary(corp_code: str) -> dict:
    return {
        "corp_code": corp_code,
        "company_name": "Test Corp",
        "ticker": "000000",
        "beneish_years": [],
        "cb_bw_count": 0,
        "cb_bw_flagged_count": 0,
        "cb_bw_max_flags": 0,
        "cb_bw_flag_types": [],
        "timing_anomaly_count": 0,
        "timing_flagged_count": 0,
        "officer_network_centrality": None,
        "officer_network_appears_in_multiple": False,
    }


def test_status_endpoint(monkeypatch):
    """GET /api/status returns 200 with artifacts and summary keys."""
    monkeypatch.setattr(app_module, "get_status", lambda: _minimal_status())
    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert "artifacts" in data
    assert "summary" in data


def test_quality_endpoint(monkeypatch):
    """GET /api/quality returns 200 with tables key."""
    monkeypatch.setattr(app_module, "get_quality", lambda: _minimal_quality())
    r = client.get("/api/quality")
    assert r.status_code == 200
    assert "tables" in r.json()


def test_summary_invalid_corp_code():
    """GET /api/companies/<non-digits>/summary returns 422."""
    r = client.get("/api/companies/abcxyz/summary")
    assert r.status_code == 422


def test_summary_corp_code_too_long():
    """GET /api/companies/<9-digit>/summary returns 422."""
    r = client.get("/api/companies/123456789/summary")
    assert r.status_code == 422


def test_summary_valid_corp_code(monkeypatch):
    """GET /api/companies/01051092/summary returns 200 with correct corp_code."""
    monkeypatch.setattr(
        app_module, "get_company_summary",
        lambda corp_code: _minimal_summary(corp_code),
    )
    r = client.get("/api/companies/01051092/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["corp_code"] == "01051092"
    assert data["company_name"] == "Test Corp"


# ─── Phase 3 stub endpoints ─────────────────────────────────────────────────


def test_alerts_endpoint_empty():
    """GET /api/alerts returns 200 with total == 0."""
    r = client.get("/api/alerts")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 0
    assert data["alerts"] == []


def test_monitor_status_endpoint():
    """GET /api/monitor/status returns 200 with running == false."""
    r = client.get("/api/monitor/status")
    assert r.status_code == 200
    data = r.json()
    assert data["running"] is False
    assert data["sources"] == []
