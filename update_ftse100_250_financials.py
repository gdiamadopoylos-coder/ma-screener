#!/usr/bin/env python3
"""
update_ftse100_250_financials.py

Enriches a Universe XLSX (FTSE100+FTSE250) with *best-effort* financial fields using Yahoo Finance
via yfinance.

Important:
- Yahoo Finance coverage is imperfect for UK names, and fields may be missing.
- This script will NEVER overwrite analyst inputs (Reg_Risk / Overlap / synergy fields).
- It will populate blanks only, unless you pass --overwrite.

Usage:
  python3 update_ftse100_250_financials.py --input ftse100_250_universe_template.xlsx --output ftse100_250_universe.xlsx
Optional:
  --overwrite   (replace existing numeric values if present)
"""

from __future__ import annotations
import argparse
import math
import time
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

NUMERIC_COLS = ["Revenue","EBITDA","EBITDA_Margin","NetDebt_EBITDA","Revenue_Growth_3Y","EV","Employees"]

def _safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        if isinstance(x, (int, float, np.integer, np.floating)):
            if math.isnan(float(x)):
                return None
            return float(x)
        s = str(x).strip().replace(",", "")
        if s == "" or s.lower() in {"nan","none","null"}:
            return None
        return float(s)
    except Exception:
        return None

def _get_info(t: yf.Ticker) -> Dict[str, Any]:
    # yfinance changed over time; try multiple access patterns.
    try:
        return t.get_info()
    except Exception:
        try:
            return t.info or {}
        except Exception:
            return {}

def _get_financials(t: yf.Ticker) -> Tuple[Optional[float], Optional[float]]:
    """
    Returns (revenue, ebitda) as best-effort TTM-ish or latest annual.
    """
    rev = ebitda = None
    # Try fast_info / info first
    info = _get_info(t)
    # revenue: totalRevenue; ebitda: ebitda
    rev = _safe_float(info.get("totalRevenue"))
    ebitda = _safe_float(info.get("ebitda"))

    if rev is None or ebitda is None:
        # Try financial statements (annual)
        try:
            fin = t.financials  # income statement
            if fin is not None and not fin.empty:
                # Try common labels
                for label in ["Total Revenue", "TotalRevenue", "Revenue"]:
                    if label in fin.index:
                        rev = rev or _safe_float(fin.loc[label].iloc[0])
                        break
                for label in ["EBITDA", "Ebitda"]:
                    if label in fin.index:
                        ebitda = ebitda or _safe_float(fin.loc[label].iloc[0])
                        break
        except Exception:
            pass

    return rev, ebitda

def _get_enterprise_value(info: Dict[str, Any]) -> Optional[float]:
    return _safe_float(info.get("enterpriseValue"))

def _get_employees(info: Dict[str, Any]) -> Optional[float]:
    return _safe_float(info.get("fullTimeEmployees"))

def _get_netdebt_to_ebitda(info: Dict[str, Any]) -> Optional[float]:
    # Yahoo sometimes has "netDebtToEBITDA"
    return _safe_float(info.get("netDebtToEBITDA"))

def _get_revenue_growth_3y(info: Dict[str, Any]) -> Optional[float]:
    # Yahoo "revenueGrowth" is typically YoY; not 3Y.
    # We keep it blank unless present; you can swap in a better data source later.
    return None

def update_row(row: pd.Series, overwrite: bool=False, sleep_s: float=0.2) -> pd.Series:
    ticker = str(row.get("Ticker","")).strip()
    if not ticker:
        return row

    # Determine if we need work
    if not overwrite:
        # If all numeric fields already populated, skip
        if all(_safe_float(row.get(c)) is not None for c in ["Revenue","EBITDA","EBITDA_Margin","EV","Employees"]):
            return row

    t = yf.Ticker(ticker)
    info = _get_info(t)

    rev, ebitda = _get_financials(t)
    ev = _get_enterprise_value(info)
    emp = _get_employees(info)
    nd_ebitda = _get_netdebt_to_ebitda(info)
    rev_g3y = _get_revenue_growth_3y(info)

    # Compute margin if possible
    margin = None
    if rev is not None and ebitda is not None and rev != 0:
        margin = float(ebitda) / float(rev)

    def put(col: str, val: Optional[float]):
        if val is None:
            return
        if overwrite or _safe_float(row.get(col)) is None:
            row[col] = val

    put("Revenue", rev)
    put("EBITDA", ebitda)
    put("EBITDA_Margin", margin)
    put("EV", ev)
    put("Employees", emp)
    put("NetDebt_EBITDA", nd_ebitda)
    put("Revenue_Growth_3Y", rev_g3y)

    # Be polite to Yahoo
    time.sleep(sleep_s)
    return row

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing numeric values")
    ap.add_argument("--sleep", type=float, default=0.2, help="Delay between tickers (seconds)")
    args = ap.parse_args()

    df = pd.read_excel(args.input, sheet_name="Universe")
    # Ensure expected columns exist
    for c in NUMERIC_COLS:
        if c not in df.columns:
            df[c] = ""

    print(f"Rows: {len(df)}. Updating (best-effort) via Yahoo Finance...")
    updated = []
    for i, row in df.iterrows():
        ticker = str(row.get("Ticker","")).strip()
        if ticker:
            print(f"[{i+1}/{len(df)}] {ticker} ...", end="")
        row2 = update_row(row.copy(), overwrite=args.overwrite, sleep_s=args.sleep)
        if ticker:
            r = _safe_float(row2.get("Revenue"))
            e = _safe_float(row2.get("EBITDA"))
            print(f" rev={'OK' if r is not None else '--'} ebitda={'OK' if e is not None else '--'}")
        updated.append(row2)

    out_df = pd.DataFrame(updated)

    # Write back
    with pd.ExcelWriter(args.output, engine="openpyxl") as w:
        out_df.to_excel(w, sheet_name="Universe", index=False)
        # Carry README if present
        try:
            readme = pd.read_excel(args.input, sheet_name="README")
            readme.to_excel(w, sheet_name="README", index=False)
        except Exception:
            pass

    print(f"✅ Wrote updated universe to {args.output}")
    print("Note: blanks are normal. For production-grade numbers, plug in a paid fundamentals API later.")

if __name__ == "__main__":
    main()
