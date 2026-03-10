"""app.py — FastAPI web application for kr-forensic-finance.

Start with:
    krff serve              # http://127.0.0.1:8000
    krff serve --reload     # dev mode with auto-reload
    uvicorn app:app --reload

API endpoints (preserved):
    GET /api/status                             → PipelineStatus
    GET /api/quality                            → DataQuality
    GET /api/companies/{corp_code}/summary      → CompanySummary
    GET /api/companies/{corp_code}/report       → HTML
    GET /api/alerts                             → AlertList
    GET /api/monitor/status                     → MonitorStatus

Web routes (new):
    GET /                                       → index.html (ranking table)
    GET /about                                  → about.html (methodology)
    GET /demo                                   → demo.html (3 demo companies)
    GET /demo/{corp_code}/report                → report (allowlist-gated)
    GET /report/{corp_code}                     → report_shell.html (iframe)
    GET /report/{corp_code}/raw                 → naked HTML report
    GET /contact                                → contact.html
    GET /datasets                               → datasets.html (standalone datasets catalogue)
    GET /privacy                                → privacy.html
    GET /terms                                  → terms.html
    GET /docs                                   → Swagger UI (auto-generated)
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time as _time
from contextlib import asynccontextmanager
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from cachetools import TTLCache
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.db import async_query, parquet_path, to_duckdb_path
from src.models import AlertList, CompanySummary, DataQuality, MonitorStatus, PipelineStatus
from src.quality import get_quality
from src.report import get_company_summary, get_report_html
from src.status import get_status

# MCP server — optional; gracefully absent if fastmcp not installed
try:
    from fastmcp.utilities.lifespan import combine_lifespans
    from src.mcp_server import mcp_server as _mcp_module
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

log = logging.getLogger(__name__)

# ── Analysis thresholds — env-var overridable ────────────────────────────────
BENEISH_THRESHOLD: float = float(os.environ.get("BENEISH_THRESHOLD", "-1.78"))
BOOTSTRAP_THRESHOLD: float = float(os.environ.get("BOOTSTRAP_THRESHOLD", "-2.45"))

# ── CORS allowed origin ──────────────────────────────────────────────────────
# Set ALLOWED_ORIGIN to your domain in production (e.g. https://krff.example.com).
# This is read from the process environment at import time; set it in the
# systemd/Docker env, not in .env (which is loaded after module import).
_ALLOWED_ORIGIN: str = os.environ.get("ALLOWED_ORIGIN", "*")

# ── Demo corps allowlist ────────────────────────────────────────────────────
# Set DEMO_CORPS env var to a comma-separated list of 8-digit corp_codes.
_demo_env = os.environ.get("DEMO_CORPS", "")
DEMO_CORPS: frozenset[str] = frozenset(
    c.strip().zfill(8) for c in _demo_env.split(",") if c.strip()
)

# ── Coverage universe (populated at startup from parquets) ──────────────────
_flagged_corps: frozenset[str] = frozenset()

# ── Approval-driven tiers (populated at startup from review_queue.db) ────────
_approved_free: frozenset[str] = frozenset()  # approved for public free tier
_approved_cache_ts: float = 0.0               # monotonic timestamp of last DB read
_APPROVED_TTL: float = 30.0                   # seconds before re-reading from SQLite

# ── TTL caches ──────────────────────────────────────────────────────────────
_report_cache: TTLCache[str, str] = TTLCache(maxsize=100, ttl=3600)
_report_lock = threading.Lock()

_summary_cache: TTLCache[str, dict] = TTLCache(maxsize=256, ttl=300)
_summary_lock = threading.Lock()

_index_cache: TTLCache[tuple, dict] = TTLCache(maxsize=50, ttl=60)
_index_lock = threading.Lock()

# ── Error page messages ──────────────────────────────────────────────────────
_ERROR_MESSAGES = {
    404: "페이지를 찾을 수 없습니다.",
    422: "잘못된 요청입니다.",
    500: "서버 오류가 발생했습니다.",
}

# ── Templates + static ──────────────────────────────────────────────────────
_BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(_BASE_DIR / "web" / "templates"))

# Note: contact_email / operator_name are injected into templates.env.globals
# inside lifespan() after load_dotenv() runs, so .env is read before values are set.


# ── Approval hot-reload ──────────────────────────────────────────────────────
def _refresh_approved() -> None:
    """Reload _approved_free from SQLite if the TTL has expired."""
    global _approved_free, _approved_cache_ts
    if _time.monotonic() - _approved_cache_ts > _APPROVED_TTL:
        from src.review import get_visible
        _approved_free = get_visible("free")
        _approved_cache_ts = _time.monotonic()


# ── DuckDB query with timeout ────────────────────────────────────────────────
def _query_with_timeout(sql: str, params: list, timeout_s: float = 10.0):
    """Run a DuckDB query in a thread; interrupt + close if it exceeds timeout."""
    import duckdb
    import pandas as pd

    result_holder: list = []
    exc_holder: list = []

    def _run():
        con = duckdb.connect()
        try:
            df = con.execute(sql, params).fetchdf()
            result_holder.append(df)
        except Exception as exc:
            exc_holder.append(exc)
        finally:
            con.close()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    if t.is_alive():
        log.warning("DuckDB query timed out after %.1fs", timeout_s)
        return pd.DataFrame()
    if exc_holder:
        log.warning("DuckDB query error: %s", exc_holder[0])
        import pandas as pd
        return pd.DataFrame()
    return result_holder[0] if result_holder else __import__("pandas").DataFrame()


# ── Report cache helpers ─────────────────────────────────────────────────────
def get_report_html_cached(corp_code: str, skip_claude: bool = True) -> str:
    with _report_lock:
        if corp_code in _report_cache:
            return _report_cache[corp_code]
    html = get_report_html(corp_code, skip_claude=skip_claude)
    with _report_lock:
        _report_cache[corp_code] = html
    return html


def get_company_summary_cached(corp_code: str) -> dict:
    with _summary_lock:
        if corp_code in _summary_cache:
            return _summary_cache[corp_code]
    summary = get_company_summary(corp_code)
    with _summary_lock:
        _summary_cache[corp_code] = summary
    return summary


# ── Lifespan: precompute demo reports on startup ─────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Preload HTML reports for demo corps and build flagged-corps universe."""
    import datetime
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    global _flagged_corps, _approved_free

    # Load .env here (not at import time) so test imports don't pick up R2 credentials
    load_dotenv()
    templates.env.globals["contact_email"] = os.environ.get("CONTACT_EMAIL", "")
    templates.env.globals["operator_name"] = os.environ.get("OPERATOR_NAME", "")

    # App version from package metadata
    try:
        _app_version = _pkg_version("kr-forensic-finance")
    except PackageNotFoundError:
        _app_version = "dev"

    # Build coverage universe + compute template globals from beneish_scores
    beneish_path = parquet_path("beneish_scores")
    cb_path = Path("03_Analysis/cb_bw_summary.csv")
    flagged: set[str] = set()
    _last_updated = "—"
    _data_period_start = "—"
    _data_period_end = "—"
    _total_company_years = 0
    _flagged_count = 0

    if beneish_path.exists():
        df = pd.read_parquet(beneish_path, columns=["corp_code", "m_score", "year"])
        flagged |= set(
            df.loc[df["m_score"] > BENEISH_THRESHOLD, "corp_code"].astype(str).str.zfill(8)
        )
        _total_company_years = len(df)
        _flagged_count = int((df["m_score"] > BENEISH_THRESHOLD).sum())
        _last_updated = datetime.datetime.fromtimestamp(
            beneish_path.stat().st_mtime
        ).strftime("%Y-%m-%d")
        years = df["year"].dropna()
        if not years.empty:
            _data_period_start = str(int(years.min()))
            _data_period_end = str(int(years.max()))

    if cb_path.exists():
        cb = pd.read_csv(cb_path, usecols=["corp_code", "flag_count"])
        flagged |= set(cb.loc[cb["flag_count"] >= 1, "corp_code"].astype(str).str.zfill(8))
    _flagged_corps = frozenset(flagged)
    log.info("Flagged corps universe: %d companies", len(_flagged_corps))

    # Unique listed companies from corp_ticker_map
    _unique_companies = 0
    cmap_path = parquet_path("corp_ticker_map")
    if cmap_path.exists():
        _unique_companies = len(pd.read_parquet(cmap_path, columns=["corp_code"]))

    # Inject all template globals — available in every template without per-route passing
    templates.env.globals.update({
        "beneish_threshold": BENEISH_THRESHOLD,
        "bootstrap_threshold": BOOTSTRAP_THRESHOLD,
        "app_version": _app_version,
        "last_updated": _last_updated,
        "data_period_start": _data_period_start,
        "data_period_end": _data_period_end,
        "total_company_years": f"{_total_company_years:,}",
        "flagged_count": f"{_flagged_count:,}",
        "unique_companies": f"{_unique_companies:,}",
        "seibro_active": bool(os.environ.get("SEIBRO_API_KEY", "")),
    })

    # Build visible-free set from review_queue.db
    from src.review import get_visible
    _approved_free = get_visible("free")
    _approved_cache_ts = _time.monotonic()
    log.info("Visible free-tier corps: %d companies", len(_approved_free))

    for corp_code in DEMO_CORPS:
        try:
            await asyncio.to_thread(get_report_html_cached, corp_code)
            log.info("Preloaded report for %s", corp_code)
        except Exception as exc:
            log.warning("Could not preload report for %s: %s", corp_code, exc)
    yield


# ── App ──────────────────────────────────────────────────────────────────────
if _MCP_AVAILABLE:
    _mcp_app = _mcp_module.http_app(path="/")
    _lifespan = combine_lifespans(lifespan, _mcp_app.lifespan)
else:
    _mcp_app = None
    _lifespan = lifespan

app = FastAPI(
    title="kr-forensic-finance",
    description="Anomaly screening API for Korean listed companies",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_ALLOWED_ORIGIN],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(_BASE_DIR / "web" / "static")), name="static")

if _mcp_app is not None:
    app.mount("/mcp", _mcp_app)


# ── Exception handlers ───────────────────────────────────────────────────────
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "status_code": exc.status_code,
            "message": _ERROR_MESSAGES.get(exc.status_code, "오류가 발생했습니다."),
            "detail": exc.detail or "",
        },
        status_code=exc.status_code,
    )


# ── Validation helpers ───────────────────────────────────────────────────────
def _validate_corp_code(corp_code: str) -> str:
    """Validate and zero-pad corp_code; raise 422 if not 1–8 digits."""
    if not corp_code.isdigit() or not (1 <= len(corp_code) <= 8):
        raise HTTPException(status_code=422, detail="corp_code must be 1–8 digits")
    return corp_code.zfill(8)


def _require_in_universe(corp_code: str) -> None:
    """Raise 404 if corp_code is not in the flagged coverage universe.

    Guard is skipped when ``_flagged_corps`` is empty (cold-start / no data),
    allowing graceful degradation without breaking the app.
    """
    if _flagged_corps and corp_code not in _flagged_corps:
        raise HTTPException(
            status_code=404,
            detail="이 회사는 현재 커버리지 유니버스에 포함되어 있지 않습니다.",
        )


def _classify_corp(corp_code: str) -> str:
    """Classify corp as 'flagged' or 'clean'. Raise 404 if not in public allowlist.

    Decision tree:
      1. If _approved_free non-empty AND corp not in it → 404 (not yet approved)
      2. If _approved_free empty AND _flagged_corps non-empty AND corp not in it → 404
      3. If corp in _flagged_corps → return 'flagged'
      4. Otherwise → return 'clean'  (covered, no anomalies detected)
    """
    _refresh_approved()
    if _approved_free and corp_code not in _approved_free:
        raise HTTPException(
            status_code=404,
            detail="이 회사는 공개 커버리지에 포함되어 있지 않습니다.",
        )
    if not _approved_free and _flagged_corps and corp_code not in _flagged_corps:
        raise HTTPException(
            status_code=404,
            detail="이 회사는 현재 커버리지 유니버스에 포함되어 있지 않습니다.",
        )
    if _flagged_corps and corp_code in _flagged_corps:
        return "flagged"
    return "clean"


# ════════════════════════════════════════════════════════════════════════════
# WEB ROUTES
# ════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(request: Request, page: int = 1, per_page: int = 25):
    """Home page — ranked company table from beneish_scores.parquet."""
    _refresh_approved()
    _approved_snapshot = tuple(sorted(_approved_free))
    _cache_key = (page, per_page, _approved_snapshot)
    with _index_lock:
        if _cache_key in _index_cache:
            cached = _index_cache[_cache_key]
            return templates.TemplateResponse("index.html", {"request": request, **cached})

    path = parquet_path("beneish_scores")
    companies = []
    stats: dict = {}
    total_pages = 1

    if path.exists():
        path_str = to_duckdb_path(path)
        offset = (page - 1) * per_page

        if _approved_free:
            codes = sorted(_approved_free)
            placeholders = ", ".join("?" * len(codes))
            sql = (
                "SELECT corp_code, company_name AS corp_name, ticker, m_score, year, "
                "0 AS flag_count "
                "FROM read_parquet(?) "
                "WHERE m_score IS NOT NULL "
                f"AND LPAD(CAST(corp_code AS VARCHAR), 8, '0') IN ({placeholders}) "
                "ORDER BY m_score DESC "
                f"LIMIT {per_page} OFFSET {offset}"
            )
            params: list = [path_str] + codes
            count_sql = (
                "SELECT COUNT(*) AS n, "
                f"SUM(CASE WHEN m_score > {BENEISH_THRESHOLD} THEN 1 ELSE 0 END) AS flagged "
                f"FROM read_parquet(?) "
                f"WHERE LPAD(CAST(corp_code AS VARCHAR), 8, '0') IN ({placeholders})"
            )
            count_params: list = [path_str] + codes
        else:
            sql = (
                "SELECT corp_code, company_name AS corp_name, ticker, m_score, year, "
                "0 AS flag_count "
                "FROM read_parquet(?) "
                "WHERE m_score IS NOT NULL "
                "ORDER BY m_score DESC "
                f"LIMIT {per_page} OFFSET {offset}"
            )
            params = [path_str]
            count_sql = (
                "SELECT COUNT(*) AS n, "
                f"SUM(CASE WHEN m_score > {BENEISH_THRESHOLD} THEN 1 ELSE 0 END) AS flagged "
                "FROM read_parquet(?)"
            )
            count_params = [path_str]

        df = await asyncio.to_thread(_query_with_timeout, sql, params)
        count_df = await asyncio.to_thread(_query_with_timeout, count_sql, count_params)

        if not count_df.empty:
            total_rows = int(count_df.iloc[0]["n"])
            flagged = int(count_df.iloc[0]["flagged"])
            total_pages = max(1, (total_rows + per_page - 1) // per_page)
            stats = {
                "total_rows": f"{total_rows:,}",
                "flagged": f"{flagged:,}",
            }

        if not df.empty:
            companies = df.to_dict(orient="records")

    # Recent signals: top 3 flagged rows from timing_anomalies.csv
    recent_signals: list[dict] = []
    ta_path = Path("03_Analysis/timing_anomalies.csv")
    if ta_path.exists():
        try:
            ta = pd.read_csv(ta_path, usecols=["corp_code", "filing_date", "title", "anomaly_score", "flag", "is_material"])
            ta = ta[ta["flag"] & ta["is_material"]].sort_values("anomaly_score", ascending=False).head(3)
            # Attach company names from corp_ticker_map
            cmap_path = parquet_path("corp_ticker_map")
            if cmap_path.exists():
                cmap = pd.read_parquet(cmap_path, columns=["corp_code", "corp_name"])
                cmap["corp_code"] = cmap["corp_code"].astype(str).str.zfill(8)
                ta["corp_code"] = ta["corp_code"].astype(str).str.zfill(8)
                ta = ta.merge(cmap.drop_duplicates("corp_code"), on="corp_code", how="left")
            else:
                ta["corp_name"] = ta["corp_code"]
            recent_signals = ta[["corp_code", "corp_name", "filing_date", "title", "anomaly_score"]].to_dict(orient="records")
        except Exception as exc:
            log.warning("Could not load recent signals: %s", exc)

    _payload = {
        "active_page": "home",
        "companies": companies,
        "stats": stats,
        "page": page,
        "total_pages": total_pages,
        "recent_signals": recent_signals,
        "is_public_sample": bool(_approved_free),
    }
    with _index_lock:
        _index_cache[_cache_key] = _payload
    return templates.TemplateResponse("index.html", {"request": request, **_payload})


@app.get("/about", response_class=HTMLResponse, include_in_schema=False)
async def about(request: Request):
    return templates.TemplateResponse(
        "about.html", {"request": request, "active_page": "about"}
    )


@app.get("/demo", response_class=HTMLResponse, include_in_schema=False)
async def demo(request: Request):
    """Demo page — show summary rows for DEMO_CORPS companies."""
    path = parquet_path("beneish_scores")
    demo_companies = []

    for corp_code in sorted(DEMO_CORPS):
        row: dict = {"corp_code": corp_code, "corp_name": corp_code, "m_score": None, "flag_count": 0, "signal_summary": "—"}
        if path.exists():
            path_str = to_duckdb_path(path)
            sql = (
                "SELECT corp_code, company_name AS corp_name, m_score, year "
                "FROM read_parquet(?) "
                "WHERE LPAD(CAST(corp_code AS VARCHAR), 8, '0') = ? "
                "ORDER BY year DESC LIMIT 1"
            )
            df = await asyncio.to_thread(_query_with_timeout, sql, [path_str, corp_code])
            if not df.empty:
                r = df.iloc[0].to_dict()
                row.update(r)
                row["corp_code"] = str(r.get("corp_code", corp_code)).zfill(8)
        demo_companies.append(row)

    return templates.TemplateResponse(
        "demo.html", {"request": request, "active_page": "demo", "demo_companies": demo_companies}
    )


@app.get("/demo/{corp_code}/report", response_class=HTMLResponse, include_in_schema=False)
async def demo_report(request: Request, corp_code: str):
    """Serve a demo report only if corp_code is in DEMO_CORPS allowlist."""
    corp_code = _validate_corp_code(corp_code)
    if corp_code not in DEMO_CORPS:
        # CTA response for non-demo companies
        html = (
            "<html><head><meta charset='UTF-8'></head><body style='font-family:sans-serif;padding:40px;'>"
            f"<h2>데모 보고서 미제공</h2>"
            f"<p>corp_code <strong>{corp_code}</strong>는 공개 데모 대상이 아닙니다.</p>"
            "<p>전체 데이터 접근을 원하시면 <a href='/contact'>문의하기</a>를 통해 연락 주세요.</p>"
            "</body></html>"
        )
        return HTMLResponse(content=html, status_code=200)
    html = await asyncio.to_thread(get_report_html_cached, corp_code)
    return HTMLResponse(content=html)


@app.get("/report/{corp_code}/raw", response_class=HTMLResponse, include_in_schema=False)
async def report_raw(corp_code: str):
    """Return naked HTML report (no nav/footer) for iframe embedding."""
    corp_code = _validate_corp_code(corp_code)
    tier = _classify_corp(corp_code)
    if tier == "clean":
        raise HTTPException(status_code=404, detail="이 회사는 이상 신호 보고서가 없습니다.")
    html = await asyncio.to_thread(get_report_html_cached, corp_code)
    return HTMLResponse(content=html)


@app.get("/report/{corp_code}", response_class=HTMLResponse, include_in_schema=False)
async def report_shell(request: Request, corp_code: str):
    """Report page with nav + iframe (flagged) or clean-company page (clean)."""
    corp_code = _validate_corp_code(corp_code)
    tier = _classify_corp(corp_code)
    if tier == "flagged":
        return templates.TemplateResponse(
            "report_shell.html", {"request": request, "corp_code": corp_code, "active_page": "report"}
        )
    # Clean company — look up name then serve informational page
    corp_name = corp_code  # fallback
    cmap_path = parquet_path("corp_ticker_map")
    if cmap_path.exists():
        cmap = await asyncio.to_thread(
            pd.read_parquet, cmap_path, columns=["corp_code", "corp_name"]
        )
        cmap["corp_code"] = cmap["corp_code"].astype(str).str.zfill(8)
        row = cmap[cmap["corp_code"] == corp_code]
        if not row.empty:
            corp_name = row.iloc[0]["corp_name"]
    return templates.TemplateResponse(
        "report_clean.html",
        {"request": request, "corp_code": corp_code, "corp_name": corp_name, "active_page": "report"},
    )


@app.get("/datasets", response_class=HTMLResponse, include_in_schema=False)
async def datasets(request: Request):
    return templates.TemplateResponse(
        "datasets.html", {"request": request, "active_page": "datasets"}
    )


@app.get("/contact", response_class=HTMLResponse, include_in_schema=False)
async def contact(request: Request):
    web3forms_key = os.environ.get("WEB3FORMS_KEY", "")
    return templates.TemplateResponse(
        "contact.html",
        {"request": request, "active_page": "contact", "web3forms_key": web3forms_key},
    )


@app.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
async def privacy(request: Request):
    return templates.TemplateResponse(
        "privacy.html", {"request": request, "active_page": "privacy"}
    )


@app.get("/terms", response_class=HTMLResponse, include_in_schema=False)
async def terms(request: Request):
    return templates.TemplateResponse(
        "terms.html", {"request": request, "active_page": "terms"}
    )


# ════════════════════════════════════════════════════════════════════════════
# API ROUTES (preserved from original)
# ════════════════════════════════════════════════════════════════════════════

@app.get("/api/companies/{corp_code}/summary", response_model=CompanySummary)
async def company_summary(corp_code: str):
    """Return structured forensic summary for one company."""
    corp_code = _validate_corp_code(corp_code)
    return await asyncio.to_thread(get_company_summary_cached, corp_code)


@app.get("/api/companies/{corp_code}/report", response_class=HTMLResponse)
async def company_report(corp_code: str, skip_claude: bool = True):
    """Return full HTML forensic report for one company."""
    corp_code = _validate_corp_code(corp_code)
    return await asyncio.to_thread(get_report_html_cached, corp_code)


@app.get("/api/status", response_model=PipelineStatus)
async def pipeline_status():
    """Return pipeline artifact inventory (which parquets exist, row counts)."""
    return await asyncio.to_thread(get_status)


@app.get("/api/quality", response_model=DataQuality)
async def data_quality():
    """Return data quality metrics (null rates, coverage gaps, stat test outputs)."""
    return await asyncio.to_thread(get_quality)


@app.get("/api/alerts", response_model=AlertList)
async def list_alerts():
    """Return recent alerts from the monitoring system (Phase 3 stub)."""
    return {"alerts": [], "total": 0}


@app.get("/api/monitor/status", response_model=MonitorStatus)
async def monitor_status():
    """Return monitoring system status (Phase 3 stub)."""
    return {"running": False, "sources": []}
