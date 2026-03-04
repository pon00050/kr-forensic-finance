"""
extract_corp_ticker_map.py — Build corp_ticker_map.parquet from company_list.parquet.

Reads 01_Data/raw/company_list.parquet (populated by extract_dart.py --stage company-list)
and writes 01_Data/processed/corp_ticker_map.parquet with the canonical join schema.

Phase 2 minimum: effective_from / effective_to are left null (no history tracking yet).
Full history tracking (relisting edge cases) is a Phase 4+ concern.

Schema:
    corp_code       str  — DART 8-digit identifier (zero-padded)
    ticker          str  — KRX stock code (6-digit)
    corp_name       str  — Korean company name
    market          str  — 'KOSDAQ' | 'KOSPI' | ...
    effective_from  str  — null (Phase 2 minimum)
    effective_to    str  — null (Phase 2 minimum)

Usage:
    python 02_Pipeline/extract_corp_ticker_map.py
    python 02_Pipeline/extract_corp_ticker_map.py --force
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', closefd=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(stream=sys.stdout)],
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
RAW = ROOT / "01_Data" / "raw"
PROCESSED = ROOT / "01_Data" / "processed"


def build_corp_ticker_map(force: bool = False) -> pd.DataFrame:
    """
    Read company_list.parquet and write corp_ticker_map.parquet.
    Rows with null or empty stock_code are excluded (non-listed companies).
    """
    out = PROCESSED / "corp_ticker_map.parquet"
    if out.exists() and not force:
        log.info("corp_ticker_map.parquet exists, loading cached (use --force to refresh)")
        return pd.read_parquet(out)

    src = RAW / "company_list.parquet"
    if not src.exists():
        raise FileNotFoundError(
            "01_Data/raw/company_list.parquet not found. "
            "Run: python 02_Pipeline/extract_dart.py --stage company-list"
        )

    companies = pd.read_parquet(src)

    # Drop rows without a stock code (unlisted subsidiaries, funds, etc.)
    # stock_code is already a stripped string from extract_dart.py
    listed = companies[
        companies["stock_code"].notna() & (companies["stock_code"] != "")
    ]

    df = pd.DataFrame({
        "corp_code": listed["corp_code"].astype(str).str.zfill(8),
        "ticker": listed["stock_code"],
        "corp_name": listed["corp_name"],
        "market": listed["market"],
        "effective_from": None,
        "effective_to": None,
    })

    PROCESSED.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    log.info("Written %d rows to %s", len(df), out)
    return df


def main():
    parser = argparse.ArgumentParser(description="Build corp_ticker_map.parquet")
    parser.add_argument("--force", action="store_true", help="Overwrite existing file")
    args = parser.parse_args()
    build_corp_ticker_map(force=args.force)


if __name__ == "__main__":
    main()
