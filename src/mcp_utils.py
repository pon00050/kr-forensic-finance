"""src/mcp_utils.py — Serialization helpers for MCP tool return values.

Korean financial data contains NaN, numpy types, and pd.Timestamp — none of
which are JSON-serializable by default. All MCP tools must pass return values
through df_to_json_str() or sanitize_for_json() before returning.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd


def sanitize_for_json(obj: Any) -> Any:
    """Recursively convert pandas/numpy types and NaN to JSON-safe Python types.

    Converts:
        numpy.integer  → int
        numpy.floating → float (or None if NaN/Inf)
        float NaN/Inf  → None
        pd.Timestamp   → ISO 8601 str
        pd.NaT         → None
        numpy.ndarray  → list
        dict/list      → recursed
    """
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_json(i) for i in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return None if (np.isnan(obj) or np.isinf(obj)) else float(obj)
    if isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
        return None
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if obj is pd.NaT:
        return None
    return obj


def df_to_records(df: pd.DataFrame) -> list[dict]:
    """Convert a DataFrame to a list of JSON-safe dicts.

    - Replaces NaN with None before to_dict (avoids pandas PyArrow scalar issue)
    - Runs each record through sanitize_for_json to catch residual numpy types
    """
    if df.empty:
        return []
    clean = df.where(df.notna(), other=None)
    records = clean.to_dict(orient="records")
    return [sanitize_for_json(r) for r in records]


def df_to_json_str(df: pd.DataFrame) -> str:
    """Convert a DataFrame to a JSON string safe for MCP tool return.

    Korean company names are preserved (ensure_ascii=False).
    NaN → null. numpy types → Python natives.
    """
    return json.dumps(df_to_records(df), ensure_ascii=False)


def paginate(records: list[dict], limit: int, offset: int) -> dict:
    """Wrap a list of records in a pagination envelope.

    Returns:
        {
            "results": [...],
            "total_count": int,
            "offset": int,
            "limit": int,
            "has_more": bool
        }
    """
    total = len(records)
    page = records[offset: offset + limit]
    return {
        "results": page,
        "total_count": total,
        "offset": offset,
        "limit": limit,
        "has_more": (offset + limit) < total,
    }


__all__ = ["sanitize_for_json", "df_to_records", "df_to_json_str", "paginate"]
