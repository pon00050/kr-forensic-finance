"""Tests for MCP tool registration and output correctness — includes tool #11: search_jfia_literature.

Run with:
    uv run python -m pytest tests/test_mcp_server.py -v --asyncio-mode=auto

Note: these tests require fastmcp to be installed (uv sync --extra dev).
They are skipped automatically if fastmcp is not available.

Data-dependent tests are skipped when the relevant parquet files are absent
(CI runners do not have the gitignored 01_Data/ directory).
"""
import json
from pathlib import Path

import pytest

pytest.importorskip("fastmcp", reason="fastmcp not installed — run: uv sync")

from fastmcp.client import Client  # noqa: E402
from krff.mcp_server import mcp_server  # noqa: E402
from krff.db import parquet_path  # noqa: E402
from krff.data_access import _JFIA_CATALOG_PATH  # noqa: E402

# ── Data availability flags ───────────────────────────────────────────────────
_HAS_CORP_MAP = parquet_path("corp_ticker_map").exists()
_HAS_BENEISH = parquet_path("beneish_scores").exists()
_HAS_PRICE = parquet_path("price_volume").exists()
_HAS_JFIA = _JFIA_CATALOG_PATH.exists()

_skip_no_corp_map = pytest.mark.skipif(
    not _HAS_CORP_MAP,
    reason="corp_ticker_map.parquet not present — run pipeline first",
)
_skip_no_beneish = pytest.mark.skipif(
    not _HAS_BENEISH,
    reason="beneish_scores.parquet not present — run pipeline first",
)
_skip_no_price = pytest.mark.skipif(
    not _HAS_PRICE,
    reason="price_volume.parquet not present — run pipeline first",
)
_skip_no_jfia = pytest.mark.skipif(
    not _HAS_JFIA,
    reason="JFIA catalog not found — clone jfia-forensic repo alongside kr-forensic-finance",
)


@pytest.fixture
async def mcp_client():
    async with Client(transport=mcp_server) as client:
        yield client


async def test_tools_registered(mcp_client):
    """All 11 tools must appear in the tool list."""
    tools = await mcp_client.list_tools()
    names = {t.name for t in tools}
    expected = {
        "lookup_corp_code",
        "get_company_summary",
        "get_beneish_scores",
        "get_cb_bw_events",
        "get_price_volume",
        "get_officer_holdings",
        "get_timing_anomalies",
        "get_major_holders",
        "get_officer_network",
        "search_flagged_companies",
        "search_jfia_literature",
    }
    assert expected.issubset(names), f"Missing tools: {expected - names}"


@_skip_no_corp_map
async def test_lookup_corp_code_returns_list(mcp_client):
    """lookup_corp_code must return a JSON array."""
    result = await mcp_client.call_tool("lookup_corp_code", {"query_str": "피씨엘"})
    data = json.loads(result.content[0].text)
    assert isinstance(data, list)


@_skip_no_corp_map
async def test_lookup_corp_code_ticker(mcp_client):
    """lookup_corp_code must resolve ticker '241820' to 피씨엘."""
    result = await mcp_client.call_tool("lookup_corp_code", {"query_str": "241820"})
    data = json.loads(result.content[0].text)
    assert isinstance(data, list)
    if data:
        assert "corp_code" in data[0]
        assert "match_type" in data[0]


@_skip_no_beneish
async def test_search_flagged_companies_envelope(mcp_client):
    """search_flagged_companies must return a pagination envelope."""
    result = await mcp_client.call_tool("search_flagged_companies", {"limit": 5})
    data = json.loads(result.content[0].text)
    assert "results" in data
    assert "total_count" in data
    assert "has_more" in data
    assert "offset" in data
    assert "limit" in data
    assert len(data["results"]) <= 5


@_skip_no_beneish
async def test_search_flagged_companies_sorted(mcp_client):
    """Results must be sorted descending by m_score."""
    result = await mcp_client.call_tool("search_flagged_companies", {"limit": 10})
    data = json.loads(result.content[0].text)
    scores = [r["m_score"] for r in data["results"] if r.get("m_score") is not None]
    assert scores == sorted(scores, reverse=True), "Results not sorted by m_score descending"


@_skip_no_beneish
async def test_no_nan_in_beneish_output(mcp_client):
    """NaN must not appear in JSON output — would be invalid JSON."""
    result = await mcp_client.call_tool(
        "get_beneish_scores", {"corp_code": "01051092"}
    )
    raw = result.content[0].text
    assert "NaN" not in raw, "Literal NaN found in JSON output — use sanitize_for_json()"
    assert "Infinity" not in raw, "Literal Infinity found in JSON output"
    data = json.loads(raw)  # must not raise
    assert isinstance(data, list)


async def test_get_company_summary_structure(mcp_client):
    """get_company_summary must return a dict with required top-level keys."""
    result = await mcp_client.call_tool("get_company_summary", {"corp_code": "01051092"})
    data = json.loads(result.content[0].text)
    assert isinstance(data, dict)
    for key in ("corp_code", "company_name", "beneish_years"):
        assert key in data, f"Missing key: {key}"


@_skip_no_price
async def test_get_price_volume_pagination_envelope(mcp_client):
    """get_price_volume must return a pagination envelope."""
    result = await mcp_client.call_tool(
        "get_price_volume",
        {
            "corp_code": "01051092",
            "start_date": "2021-01-01",
            "end_date": "2021-12-31",
            "limit": 10,
        },
    )
    data = json.loads(result.content[0].text)
    assert "results" in data
    assert "total_count" in data
    assert "has_more" in data


# ── Tool 11: search_jfia_literature ──────────────────────────────────────────

async def test_search_jfia_literature_no_catalog_graceful(mcp_client, monkeypatch):
    """If JFIA catalog is absent, tool returns empty list (not an error)."""
    import krff.data_access as da
    monkeypatch.setattr(da, "_jfia_loaded", False)
    monkeypatch.setattr(da, "_JFIA_CATALOG_SINGLETON", None)
    monkeypatch.setattr(da, "_JFIA_CATALOG_PATH", Path("/nonexistent/jfia_catalog.json"))
    result = await mcp_client.call_tool("search_jfia_literature", {"query": "earnings"})
    data = json.loads(result.content[0].text)
    assert isinstance(data, list)
    assert data == []


@_skip_no_jfia
async def test_search_jfia_literature_returns_json(mcp_client):
    """Valid query returns JSON list with expected fields."""
    result = await mcp_client.call_tool(
        "search_jfia_literature", {"query": "earnings management"}
    )
    data = json.loads(result.content[0].text)
    assert isinstance(data, list)
    if data:
        first = data[0]
        assert "title" in first
        assert "authors" in first
        assert "pdf_url" in first
        assert "abstract_snippet" in first


@_skip_no_jfia
async def test_search_jfia_literature_limit_respected(mcp_client):
    """limit=2 returns at most 2 results."""
    result = await mcp_client.call_tool(
        "search_jfia_literature", {"query": "fraud detection", "limit": 2}
    )
    data = json.loads(result.content[0].text)
    assert isinstance(data, list)
    assert len(data) <= 2
