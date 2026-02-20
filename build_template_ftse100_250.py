#!/usr/bin/env python3
"""
build_template_ftse100_250.py

Creates a populated universe template by pulling *current* FTSE 100 + FTSE 250 constituents
from London Stock Exchange JSON endpoints, then writing an XLSX in the same column format
your Streamlit app expects.

Usage:
  python3 build_template_ftse100_250.py --output ftse100_250_universe_template.xlsx
"""

from __future__ import annotations
import argparse
import datetime as dt
import sys
from typing import Dict, List

import pandas as pd
import requests

FTSE100_URL = "https://prod-aws.londonstockexchange.com/indices/ftse-100/constituents"
FTSE250_URL = "https://prod-aws.londonstockexchange.com/indices/ftse-250/constituents"

OUT_COLUMNS = [
    "IndexBucket","Company","Ticker","Country","Sector",
    "Revenue","EBITDA","EBITDA_Margin","NetDebt_EBITDA",
    "Revenue_Growth_3Y","Reg_Risk","Overlap",
    "EV","Employees",
    "Rev_Synergy_Potential","Cost_Synergy_Potential","Integration_Complexity",
]

def fetch_constituents(url: str) -> List[Dict]:
    """
    LSE endpoint typically returns JSON. We try robust parsing and fail loudly if the shape changes.
    """
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    try:
        data = r.json()
    except Exception:
        # Sometimes endpoints return JSON with a content-type that confuses json()
        data = requests.get(url, headers={"Accept":"application/json"}, timeout=30).json()

    # Common shapes observed: {"data":[...]} or {"constituents":[...]} or list directly.
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        if "data" in data and isinstance(data["data"], list):
            rows = data["data"]
        elif "constituents" in data and isinstance(data["constituents"], list):
            rows = data["constituents"]
        else:
            # Fall back: find the first list value
            rows = None
            for v in data.values():
                if isinstance(v, list):
                    rows = v
                    break
            if rows is None:
                raise ValueError(f"Unexpected JSON shape keys={list(data.keys())[:20]}")
    else:
        raise ValueError(f"Unexpected JSON type: {type(data)}")
    return rows

def normalize(rows: List[Dict], bucket: str) -> pd.DataFrame:
    """
    Map LSE fields -> our template. We keep analyst fields at defaults so the app can run.
    """
    out = []
    for r in rows:
        # Best-effort field names
        name = (r.get("companyName") or r.get("name") or r.get("constituentName") or "").strip()
        epic = (r.get("tidm") or r.get("epic") or r.get("symbol") or r.get("instrumentId") or "").strip()

        # Some rows include "tidm" like "RR." or "RR"; we normalize to Yahoo's ".L" tickers
        ticker = ""
        if epic:
            # strip punctuation sometimes present
            epic_clean = epic.replace(".", "").replace(" ", "")
            ticker = f"{epic_clean}.L"

        sector = (r.get("industry") or r.get("sector") or r.get("icbSector") or "").strip()
        country = (r.get("country") or r.get("countryOfIncorporation") or "UK").strip() or "UK"

        out.append({
            "IndexBucket": bucket,
            "Company": name or epic,
            "Ticker": ticker,
            "Country": country,
            "Sector": sector,
            # numeric placeholders (filled by updater)
            "Revenue": "",
            "EBITDA": "",
            "EBITDA_Margin": "",
            "NetDebt_EBITDA": "",
            "Revenue_Growth_3Y": "",
            # analyst inputs (sane defaults)
            "Reg_Risk": 3 if bucket == "FTSE250" else 2,
            "Overlap": 2,
            "EV": "",
            "Employees": "",
            "Rev_Synergy_Potential": 2,
            "Cost_Synergy_Potential": 2,
            "Integration_Complexity": 2,
        })

    df = pd.DataFrame(out)
    # Ensure consistent columns
    for c in OUT_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    return df[OUT_COLUMNS]

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True, help="Path for output XLSX")
    args = ap.parse_args()

    print("Fetching FTSE 100 constituents...")
    ftse100 = normalize(fetch_constituents(FTSE100_URL), "FTSE100")

    print("Fetching FTSE 250 constituents...")
    ftse250 = normalize(fetch_constituents(FTSE250_URL), "FTSE250")

    df = pd.concat([ftse100, ftse250], ignore_index=True)
    # Drop empty tickers (rare)
    df = df[df["Ticker"].astype(str).str.len() > 2].copy()

    # Write
    with pd.ExcelWriter(args.output, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="Universe", index=False)

        meta = pd.DataFrame({
            "key": ["generated_utc", "source_ftse100", "source_ftse250", "notes"],
            "value": [
                dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
                FTSE100_URL,
                FTSE250_URL,
                "Tickers are formatted for Yahoo Finance as EPIC.L. Some may need manual correction.",
            ],
        })
        meta.to_excel(w, sheet_name="README", index=False)

    print(f"✅ Wrote {len(df)} rows to {args.output}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted.")
        sys.exit(130)
