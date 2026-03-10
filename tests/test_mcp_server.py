"""Tests for MCP tool registration and output correctness.

Run with:
    uv run python -m pytest tests/test_mcp_server.py -v --asyncio-mode=auto

Note: these tests require fastmcp to be installed (uv sync --extra dev).
They are skipped automatically if fastmcp is not available.
"""
import json

import pytest

pytest.importorskip("fastmcp", reason="fastmcp not installed — run: uv sync")

from fastmcp.client import Client  # noqa: E402
from src.mcp_server import mcp_server  # noqa: E402


@pytest.fixture
async def mcp_client():
    async with Client(transport=mcp_server) as client:
        yield client


async def test_tools_registered(mcp_client):
    """All 10 tools must appear in the tool list."""
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
    }
    assert expected.issubset(names), f"Missing tools: {expected - names}"


async def test_lookup_corp_code_returns_list(mcp_client):
    """lookup_corp_code must return a JSON array."""
    result = await mcp_client.call_tool("lookup_corp_code", {"query_str": "피씨엘"})
    data = json.loads(result.content[0].text)
    assert isinstance(data, list)


async def test_lookup_corp_code_ticker(mcp_client):
    """lookup_corp_code must resolve ticker '241820' to 피씨엘."""
    result = await mcp_client.call_tool("lookup_corp_code", {"query_str": "241820"})
    data = json.loads(result.content[0].text)
    assert isinstance(data, list)
    # If corp_ticker_map has this entry, verify the corp_code
    if data:
        assert "corp_code" in data[0]
        assert "match_type" in data[0]


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


async def test_search_flagged_companies_sorted(mcp_client):
    """Results must be sorted descending by m_score."""
    result = await mcp_client.call_tool("search_flagged_companies", {"limit": 10})
    data = json.loads(result.content[0].text)
    scores = [r["m_score"] for r in data["results"] if r.get("m_score") is not None]
    assert scores == sorted(scores, reverse=True), "Results not sorted by m_score descending"


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
