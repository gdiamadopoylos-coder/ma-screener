# app.py
# MERJURE™ — M&A Target Screener (Pilot V1)
# V2 scoring + PDF pack export + PPTX deck export
# Adds:
# - Robust logo handling (won't crash if logo missing)
# - Confidence score (data completeness) to avoid KeyError
# - Interactive radar chart (Plotly) for decision compass

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd
import streamlit as st

# Plotly for interactive radar
import plotly.graph_objects as go

# Your exporters (must exist)
from reporting.pdf_export import build_screener_pack_pdf
from ppt_export import build_merger_pptx


# ----------------------------
# Page config / branding
# ----------------------------
st.set_page_config(page_title="MERJURE™", layout="wide")


# ----------------------------
# Helpers / schema
# ----------------------------
REQUIRED_COLS = [
    "Company",
    "Ticker",
    "Country",
    "Sector",
    "Revenue",
    "EBITDA",
    "EBITDA_Margin",
    "NetDebt_EBITDA",
    "Revenue_Growth_3Y",
    "Reg_Risk",
    "Overlap",
]

NUMERIC_COLS = [
    "Revenue",
    "EBITDA",
    "EBITDA_Margin",
    "NetDebt_EBITDA",
    "Revenue_Growth_3Y",
    "Reg_Risk",
    "Overlap",
]


def _now_str_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _coerce_numeric(df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    out = df.copy()
    for c in cols:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def _validate(df: pd.DataFrame) -> Tuple[bool, List[str]]:
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    return (len(missing) == 0, missing)


def _read_file(uploaded) -> pd.DataFrame:
    name = uploaded.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded)
    if name.endswith(".xlsx") or name.endswith(".xls"):
        return pd.read_excel(uploaded)
    raise ValueError("Unsupported file type. Please upload CSV or XLSX.")


def _minmax(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce").astype(float)
    if len(s) == 0:
        return s
    mn, mx = np.nanmin(s), np.nanmax(s)
    if not np.isfinite(mn) or not np.isfinite(mx) or mx == mn:
        return pd.Series(np.zeros(len(s)), index=s.index)
    return (s - mn) / (mx - mn)


def _confidence_score(df: pd.DataFrame, cols: List[str]) -> pd.Series:
    """
    Confidence = % completeness across key numeric inputs used by the model.
    0..100
    """
    use_cols = [c for c in cols if c in df.columns]
    if not use_cols:
        return pd.Series(np.full(len(df), 0.0), index=df.index)
    present = df[use_cols].notna().sum(axis=1)
    return (present / len(use_cols) * 100.0).round(1)


def _deal_type_from_size_pct(pct: float) -> str:
    if not np.isfinite(pct):
        return "TBD"
    if pct < 10:
        return "Bolt-on"
    if pct < 30:
        return "Platform"
    return "Transformational"


def _score_v2(df_targets: pd.DataFrame, weights: Dict[str, int], horizon: str) -> pd.DataFrame:
    """
    V2 scoring (transparent):
    - Fit: Overlap normalized
    - Growth: Revenue_Growth_3Y normalized w/ horizon multiplier
    - Margin: EBITDA_Margin normalized w/ horizon multiplier
    - Risk: penalty from Reg_Risk + leverage (NetDebt_EBITDA), normalized
    Weighted sum -> Score_Total
    Also adds Confidence (data completeness).
    """
    df = df_targets.copy()

    # Horizon multipliers
    if horizon == "2–3 years":
        growth_mult = 0.9
        margin_mult = 0.9
    elif horizon == "3–5 years":
        growth_mult = 1.0
        margin_mult = 1.0
    else:  # "5–10 years"
        growth_mult = 1.1
        margin_mult = 1.1

    # Overlap normalization (0..1 or 0..100 or 1..3 etc — normalize regardless)
    overlap = pd.to_numeric(df["Overlap"], errors="coerce").astype(float)
    if len(overlap) == 0:
        overlap_scaled = overlap
    else:
        mx = np.nanmax(overlap)
        if np.isfinite(mx) and mx <= 1.0:
            overlap_scaled = overlap * 100.0
        elif np.isfinite(mx) and mx <= 3.0:
            overlap_scaled = (overlap / 3.0) * 100.0
        else:
            overlap_scaled = overlap.clip(lower=0, upper=100)

    fit_n = _minmax(overlap_scaled)
    growth_n = _minmax(pd.to_numeric(df["Revenue_Growth_3Y"], errors="coerce").astype(float)) * growth_mult
    margin_n = _minmax(pd.to_numeric(df["EBITDA_Margin"], errors="coerce").astype(float)) * margin_mult

    risk_raw = pd.to_numeric(df["Reg_Risk"], errors="coerce").astype(float) + 0.5 * pd.to_numeric(
        df["NetDebt_EBITDA"], errors="coerce"
    ).astype(float)
    risk_n = _minmax(risk_raw)

    df["Score_Fit"] = (fit_n * 100).round(1)
    df["Score_Growth"] = (growth_n * 100).round(1)
    df["Score_Margin"] = (margin_n * 100).round(1)
    df["Score_Risk"] = (risk_n * 100).round(1)

    w_fit = float(weights["strategic_fit"])
    w_growth = float(weights["growth"])
    w_margin = float(weights["margin_uplift"])
    w_risk = float(weights["risk_penalty"])

    total = (
        (df["Score_Fit"] * w_fit)
        + (df["Score_Growth"] * w_growth)
        + (df["Score_Margin"] * w_margin)
        - (df["Score_Risk"] * w_risk)
    ) / 100.0
    df["Score_Total"] = total.round(1)

    # Confidence (data quality)
    df["Confidence"] = _confidence_score(df, cols=["Revenue", "EBITDA_Margin", "NetDebt_EBITDA", "Revenue_Growth_3Y", "Reg_Risk", "Overlap"])

    def why_row(r) -> str:
        reasons = []
        if r.get("Score_Fit", 0) >= 70:
            reasons.append("High strategic overlap")
        elif r.get("Score_Fit", 0) >= 45:
            reasons.append("Moderate strategic overlap")

        if r.get("Score_Growth", 0) >= 70:
            reasons.append("Above-median growth")
        elif r.get("Score_Growth", 0) >= 45:
            reasons.append("Solid growth")

        if r.get("Score_Margin", 0) >= 70:
            reasons.append("Attractive margin profile")
        elif r.get("Score_Margin", 0) >= 45:
            reasons.append("Okay margin profile")

        if r.get("Score_Risk", 0) >= 70:
            reasons.append("Higher risk / leverage")
        elif r.get("Score_Risk", 0) <= 35:
            reasons.append("Lower risk / leverage")

        # Data quality nudge (kept short)
        if r.get("Confidence", 0) < 70:
            reasons.append("Lower data confidence")

        return "; ".join(reasons[:3]) if reasons else "Balanced profile under current weights"

    df["Top_Why_Deal_Works"] = df.apply(why_row, axis=1)
    return df


def _build_memo(
    scored: pd.DataFrame,
    df_full: pd.DataFrame,
    acquirer_name: str,
    acq_sector: str,
    acq_rev: float,
    same_sector_only: bool,
    max_size_pct: int,
    max_leverage: float,
    horizon: str,
    weights: Dict[str, int],
) -> Tuple[Dict, pd.DataFrame]:
    top5 = scored.head(5).copy()

    # add deal type based on size % of acquirer revenue
    if np.isfinite(acq_rev) and acq_rev > 0:
        top5["SizePctOfAcqRev"] = (pd.to_numeric(top5["Revenue"], errors="coerce") / acq_rev) * 100.0
    else:
        top5["SizePctOfAcqRev"] = np.nan
    top5["DealType"] = top5["SizePctOfAcqRev"].apply(_deal_type_from_size_pct)

    acq_row = df_full[df_full["Company"] == acquirer_name].iloc[0]
    acq_margin = float(acq_row["EBITDA_Margin"]) if pd.notna(acq_row["EBITDA_Margin"]) else None
    acq_growth = float(acq_row["Revenue_Growth_3Y"]) if pd.notna(acq_row["Revenue_Growth_3Y"]) else None
    acq_lev = float(acq_row["NetDebt_EBITDA"]) if pd.notna(acq_row["NetDebt_EBITDA"]) else None

    med_lev = float(np.nanmedian(pd.to_numeric(top5["NetDebt_EBITDA"], errors="coerce"))) if len(top5) else float("nan")
    med_margin = float(np.nanmedian(pd.to_numeric(top5["EBITDA_Margin"], errors="coerce"))) if len(top5) else float("nan")
    med_growth = float(np.nanmedian(pd.to_numeric(top5["Revenue_Growth_3Y"], errors="coerce"))) if len(top5) else float("nan")

    bullets: List[str] = []
    if len(top5) > 0:
        rec = top5.iloc[0]
        bullets.append(f"Recommend pursuing {rec['Company']} ({rec['DealType']}); ranks #1 on Score_Total.")
        bullets.append(f"Target data confidence: {rec.get('Confidence', np.nan)} / 100.")
    bullets.append(f"Top 5 median leverage: {med_lev:.2f}x (constraint: {max_leverage:.1f}x).")
    bullets.append(f"Top 5 median EBITDA margin: {med_margin:.2f}.")
    if acq_margin is not None and np.isfinite(med_margin):
        bullets.append(f"Top 5 margin vs acquirer: {med_margin - acq_margin:+.2f} (Top-5 median minus acquirer).")
    if acq_growth is not None and np.isfinite(med_growth):
        bullets.append(f"Top 5 growth vs acquirer: {med_growth - acq_growth:+.2f} (Top-5 median minus acquirer).")
    bullets = bullets[:5]

    memo = {
        "generated": _now_str_utc(),
        "recommendation": {
            "target": str(top5.iloc[0]["Company"]) if len(top5) else None,
            "deal_type": str(top5.iloc[0]["DealType"]) if len(top5) else None,
        },
        "bullets": bullets,
        "scenario": {
            "acquirer": acquirer_name,
            "sector": acq_sector,
            "sector_filter": "Same sector only" if same_sector_only else "All sectors",
            "horizon": horizon,
        },
        "filters": {
            "only_same_sector": "YES" if same_sector_only else "NO",
            "max_target_size_pct": str(max_size_pct),
            "max_netdebt_ebitda": str(max_leverage),
        },
        "weights": {
            "strategic_fit": int(weights["strategic_fit"]),
            "growth": int(weights["growth"]),
            "margin_uplift": int(weights["margin_uplift"]),
            "risk_penalty": int(weights["risk_penalty"]),
        },
        "acquirer_metrics": {
            "revenue": float(acq_rev) if np.isfinite(acq_rev) else None,
            "ebitda_margin": acq_margin,
            "growth_3y": acq_growth,
            "leverage": acq_lev,
        },
        "top5_medians": {
            "leverage": med_lev,
            "ebitda_margin": med_margin,
            "growth_3y": med_growth,
        },
    }

    return memo, top5


def _build_pptx_payload(
    scored: pd.DataFrame,
    df_full: pd.DataFrame,
    acquirer_name: str,
    same_sector_only: bool,
    max_size_pct: int,
    max_leverage: float,
    horizon: str,
) -> Dict:
    top_n = 10
    top = scored.head(top_n).copy()

    comps_columns = ["Company", "Ticker", "Country", "Sector", "Revenue", "EBITDA", "NetDebt_EBITDA", "Score_Total", "Confidence"]
    comps_rows: List[List[str]] = []

    for _, r in top.iterrows():
        comps_rows.append(
            [
                str(r.get("Company", "")),
                str(r.get("Ticker", "")),
                str(r.get("Country", "")),
                str(r.get("Sector", "")),
                str(r.get("Revenue", "")),
                str(r.get("EBITDA", "")),
                str(r.get("NetDebt_EBITDA", "")),
                str(r.get("Score_Total", "")),
                str(r.get("Confidence", "")),
            ]
        )

    payload = {
        "target": "Top Ranked Targets",
        "acquirer": acquirer_name,
        "thesis_bullets": [
            f"Universe screened: {len(df_full)} companies",
            f"Filters: sector={('same' if same_sector_only else 'any')}, max leverage={max_leverage}, max size%={max_size_pct}",
            f"Horizon: {horizon}",
        ],
        "synergies_bullets": [
            "Commercial synergy potential (cross-sell / adjacency)",
            "Cost synergy potential (scale / overlap rationalisation)",
            "Balance sheet / financing compatibility",
        ],
        "risks_bullets": [
            "Integration complexity (geo/ops mismatch)",
            "Regulatory / antitrust sensitivity",
            "Data completeness risk (low confidence reduces defensibility)",
        ],
        "comps_columns": comps_columns,
        "comps_rows": comps_rows,
        "sources_bullets": [
            "Input universe file (user supplied CSV/XLSX)",
            "MERJURE scoring model (Pilot V1)",
        ],
    }
    return payload


def _find_logo_path() -> Optional[str]:
    """
    Tries a handful of likely filenames in:
    - ./ (project root)
    - ./assets/
    Returns a path string if found else None.
    """
    candidates = [
        "MJ.PNG", "MJ.png", "MJPNG.png", "MJPNG.PNG",
        "MERJURE_LOGO.PNG", "MERJURE_LOGO.png", "MERJURE_LOGO.jpg", "MERJURE_LOGO.jpeg",
        "merjure_logo.png", "merjure_logo.jpg",
        "MERJURE_LOGO.j",  # in case you had a weird rename (seen in screenshots)
    ]
    roots = [Path("."), Path("./assets")]

    for root in roots:
        for name in candidates:
            p = root / name
            if p.exists() and p.is_file():
                return str(p)

    # If you have *something* in assets, pick the first image file as a last resort
    assets = Path("./assets")
    if assets.exists():
        for p in assets.iterdir():
            if p.is_file() and p.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]:
                return str(p)

    return None


def _radar_values_from_row(row: pd.Series, df_universe: pd.DataFrame) -> Dict[str, float]:
    """
    Build 0..100 radar values using normalization vs the full universe.
    Dimensions:
      - Strategic fit (Overlap)
      - Growth (Revenue_Growth_3Y)
      - Margin (EBITDA_Margin)
      - Balance sheet (inverse leverage)
      - Regulatory (inverse Reg_Risk)
      - Data confidence
    """
    # Use universe minmax so scale is consistent
    def norm(col: str, val: float) -> float:
        s = pd.to_numeric(df_universe[col], errors="coerce").astype(float)
        mn, mx = np.nanmin(s), np.nanmax(s)
        if not np.isfinite(val) or not np.isfinite(mn) or not np.isfinite(mx) or mx == mn:
            return 0.0
        return float((val - mn) / (mx - mn) * 100.0)

    # Overlap normalization same logic as scoring
    ov = float(pd.to_numeric(row.get("Overlap", np.nan), errors="coerce"))
    if np.isfinite(ov):
        # approximate scale guess
        if ov <= 1.0:
            ov_scaled = ov * 100.0
        elif ov <= 3.0:
            ov_scaled = (ov / 3.0) * 100.0
        else:
            ov_scaled = float(np.clip(ov, 0, 100))
    else:
        ov_scaled = np.nan

    fit = float(np.clip(ov_scaled if np.isfinite(ov_scaled) else 0.0, 0, 100))
    growth = norm("Revenue_Growth_3Y", float(pd.to_numeric(row.get("Revenue_Growth_3Y", np.nan), errors="coerce")))
    margin = norm("EBITDA_Margin", float(pd.to_numeric(row.get("EBITDA_Margin", np.nan), errors="coerce")))

    lev = float(pd.to_numeric(row.get("NetDebt_EBITDA", np.nan), errors="coerce"))
    lev_n = norm("NetDebt_EBITDA", lev)
    balance_sheet = float(np.clip(100.0 - lev_n, 0, 100))

    reg = float(pd.to_numeric(row.get("Reg_Risk", np.nan), errors="coerce"))
    reg_n = norm("Reg_Risk", reg)
    regulatory = float(np.clip(100.0 - reg_n, 0, 100))

    conf = float(pd.to_numeric(row.get("Confidence", np.nan), errors="coerce"))
    confidence = float(np.clip(conf if np.isfinite(conf) else 0.0, 0, 100))

    return {
        "Strategic fit": fit,
        "Growth": float(np.clip(growth, 0, 100)),
        "Margin": float(np.clip(margin, 0, 100)),
        "Balance sheet": balance_sheet,
        "Regulatory": regulatory,
        "Confidence": confidence,
    }


def _plot_radar(acq_vals: Dict[str, float], other_vals: Dict[str, float], title: str) -> go.Figure:
    labels = list(acq_vals.keys())
    acq_r = [acq_vals[k] for k in labels]
    oth_r = [other_vals[k] for k in labels]

    # Close the loop
    labels2 = labels + [labels[0]]
    acq_r2 = acq_r + [acq_r[0]]
    oth_r2 = oth_r + [oth_r[0]]

    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(r=acq_r2, theta=labels2, fill="toself", name="Acquirer"))
    fig.add_trace(go.Scatterpolar(r=oth_r2, theta=labels2, fill="toself", name="Targets"))
    fig.update_layout(
        title=title,
        polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
        showlegend=True,
        margin=dict(l=30, r=30, t=60, b=30),
        height=460,
    )
    return fig


# ----------------------------
# Header
# ----------------------------
logo_path = _find_logo_path()
cols = st.columns([0.12, 0.88])
with cols[0]:
    if logo_path:
        st.image(logo_path, use_container_width=True)
with cols[1]:
    st.markdown("## **MERJURE™**")
    st.caption("MERJURE uses AI to turn market data into acquisition decisions — faster, clearer, defensible.")
st.markdown("---")


# ----------------------------
# Upload
# ----------------------------
uploaded = st.file_uploader("Upload your company universe (CSV or XLSX)", type=["csv", "xlsx", "xls"])

if uploaded is None:
    st.info("Upload a CSV/XLSX to begin.")
    st.stop()

try:
    df_raw = _read_file(uploaded)
except Exception as e:
    st.error(f"Could not read file: {e}")
    st.stop()

ok, missing_cols = _validate(df_raw)
if not ok:
    st.error(f"Your file is missing required columns: {missing_cols}")
    st.stop()

df = df_raw.copy()
df = _coerce_numeric(df, NUMERIC_COLS)

# basic cleanup
for c in ["Company", "Sector", "Country", "Ticker"]:
    df[c] = df[c].astype(str)

# warn on missing numerics (but do not stop)
if df[["Revenue", "EBITDA_Margin", "NetDebt_EBITDA", "Revenue_Growth_3Y", "Reg_Risk", "Overlap"]].isna().any().any():
    st.warning(
        "Some numeric cells are missing/invalid. Scoring still runs, but outputs will be less defensible. "
        "Fix your universe data if this is for a real memo."
    )


# ----------------------------
# Controls + Results
# ----------------------------
left, right = st.columns([1, 1], gap="large")

with left:
    st.subheader("1) Select acquirer")
    acquirer_name = st.selectbox("Acquirer", sorted(df["Company"].unique().tolist()))

    st.subheader("2) Filters")
    same_sector_only = st.checkbox("Only same sector as acquirer", value=True)
    max_size_pct = st.slider("Max target size (% of acquirer revenue)", 5, 100, 50)
    max_leverage = st.slider("Max target NetDebt/EBITDA", 0.0, 10.0, 5.0, 0.1)

    st.subheader("3) Weights (target sum ~100)")
    w_fit = st.slider("Strategic fit (Overlap)", 0, 100, 30)
    w_growth = st.slider("Growth (Revenue_Growth_3Y)", 0, 100, 20)
    w_margin = st.slider("Margin uplift headroom (EBITDA_Margin)", 0, 100, 25)
    w_risk = st.slider("Risk penalty (Reg_Risk + leverage)", 0, 100, 25)

    weights = {
        "strategic_fit": int(w_fit),
        "growth": int(w_growth),
        "margin_uplift": int(w_margin),
        "risk_penalty": int(w_risk),
    }

    st.subheader("4) Horizon scenario")
    horizon = st.selectbox("Horizon", ["2–3 years", "3–5 years", "5–10 years"], index=0)

    w_sum = sum(weights.values())
    if not (90 <= w_sum <= 110):
        st.warning(f"Your weights sum to {w_sum}. Keep it ~100 for interpretability.")


# ----------------------------
# Compute
# ----------------------------
acq = df[df["Company"] == acquirer_name].iloc[0]
acq_rev = float(acq["Revenue"]) if pd.notna(acq["Revenue"]) else np.nan
acq_sector = str(acq["Sector"])

targets = df[df["Company"] != acquirer_name].copy()

# leverage constraint
targets = targets[pd.to_numeric(targets["NetDebt_EBITDA"], errors="coerce") <= max_leverage]

# size constraint (Revenue <= % of acquirer revenue)
if np.isfinite(acq_rev) and acq_rev > 0:
    targets = targets[pd.to_numeric(targets["Revenue"], errors="coerce") <= (max_size_pct / 100.0) * acq_rev]
else:
    st.warning("Acquirer revenue is missing/invalid. Max target size filter skipped.")

# sector constraint
if same_sector_only:
    targets = targets[targets["Sector"] == acq_sector]

if targets.empty:
    st.error("No targets match your filters. Relax constraints and try again.")
    st.stop()

scored = _score_v2(targets, weights=weights, horizon=horizon)
scored = scored.sort_values("Score_Total", ascending=False).reset_index(drop=True)


# ----------------------------
# Right panel + exports
# ----------------------------
with right:
    st.subheader("Ranked targets")
    st.write(
        f"**Acquirer:** {acquirer_name}  |  "
        f"**Sector filter:** {'Same sector only' if same_sector_only else 'All sectors'}"
    )

    top_strip = scored.head(5)[["Company", "Ticker", "Country", "Sector", "Revenue", "Score_Total", "Confidence"]].copy()
    st.dataframe(top_strip, hide_index=True, use_container_width=True)

    st.markdown("### 📦 Export results")

    memo, top5 = _build_memo(
        scored=scored,
        df_full=df,
        acquirer_name=acquirer_name,
        acq_sector=acq_sector,
        acq_rev=acq_rev,
        same_sector_only=same_sector_only,
        max_size_pct=max_size_pct,
        max_leverage=max_leverage,
        horizon=horizon,
        weights=weights,
    )

    # PPTX
    pptx_payload = _build_pptx_payload(
        scored=scored,
        df_full=df,
        acquirer_name=acquirer_name,
        same_sector_only=same_sector_only,
        max_size_pct=max_size_pct,
        max_leverage=max_leverage,
        horizon=horizon,
    )
    pptx_bytes = build_merger_pptx(pptx_payload)
    st.download_button(
        label="⬇️ Download PPTX (MERJURE deck)",
        data=pptx_bytes,
        file_name=f"merjure_{acquirer_name}_deck.pptx",
        mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        use_container_width=True,
    )

    # PDF
    filters_payload = {
        "only_same_sector": "YES" if same_sector_only else "NO",
        "max_target_size_pct": str(max_size_pct),
        "max_netdebt_ebitda": str(max_leverage),
    }

    try:
        pdf_bytes = build_screener_pack_pdf(
            out_df=scored,
            acquirer_name=acquirer_name,
            sector_filter=("Same sector only" if same_sector_only else "All sectors"),
            horizon=horizon,
            filters=filters_payload,
            weights=weights,
            memo=memo,
            top5_df=top5,
            title="MERJURE — M&A Target Screening Pack",
            logo_path=logo_path,
        )
    except TypeError:
        pdf_bytes = build_screener_pack_pdf(
            out_df=scored,
            acquirer_name=acquirer_name,
            sector_filter=("Same sector only" if same_sector_only else "All sectors"),
            horizon=horizon,
            filters=filters_payload,
            weights=weights,
            title="MERJURE — M&A Target Screening Pack",
            logo_path=logo_path,
        )

    st.download_button(
        label="⬇️ Download PDF pack",
        data=pdf_bytes,
        file_name="merjure_pack.pdf",
        mime="application/pdf",
        use_container_width=True,
    )

    st.markdown("---")
    st.subheader("Top 10 (explainable)")
    st.dataframe(
        scored.head(10)[["Company", "Ticker", "Country", "Sector", "Score_Total", "Confidence", "Top_Why_Deal_Works"]],
        hide_index=True,
        use_container_width=True,
    )


# ----------------------------
# Decision compass (interactive radar)
# ----------------------------
st.markdown("---")
st.subheader("Decision compass")

top_k = min(5, len(scored))
top_targets = scored.head(top_k).copy()

# Build radar profiles
acq_conf = float(_confidence_score(pd.DataFrame([acq]), cols=["Revenue", "EBITDA_Margin", "NetDebt_EBITDA", "Revenue_Growth_3Y", "Reg_Risk", "Overlap"]).iloc[0])
acq_row_for_radar = acq.copy()
acq_row_for_radar["Confidence"] = acq_conf

acq_vals = _radar_values_from_row(acq_row_for_radar, df_universe=df)

avg_row = top_targets[["Overlap", "Revenue_Growth_3Y", "EBITDA_Margin", "NetDebt_EBITDA", "Reg_Risk", "Confidence"]].mean(numeric_only=True)
avg_row["Company"] = f"Top {top_k} average"
avg_vals = _radar_values_from_row(avg_row, df_universe=df)

left_r, right_r = st.columns([1, 1], gap="large")

with left_r:
    mode = st.radio(
        "Compare against:",
        [f"Top {top_k} targets (average)", "Select a target"],
        horizontal=True
    )

    if mode == "Select a target":
        tgt_name = st.selectbox("Target", scored["Company"].tolist())
        tgt_row = scored[scored["Company"] == tgt_name].iloc[0]
        tgt_vals = _radar_values_from_row(tgt_row, df_universe=df)
        fig = _plot_radar(acq_vals, tgt_vals, title=f"Acquirer vs {tgt_name}")
    else:
        fig = _plot_radar(acq_vals, avg_vals, title=f"Acquirer vs Top {top_k} targets (average profile)")

    st.plotly_chart(fig, use_container_width=True)
    st.caption("Interpretation: this shows what profile your top-ranked list is pulling you toward (interactive).")

with right_r:
    st.markdown("**Heat map view (triage)**")
    # Simple triage heatmap: Score vs Confidence, sized by Revenue
    tri = scored.head(30).copy()
    tri["Revenue_num"] = pd.to_numeric(tri["Revenue"], errors="coerce")
    tri["Score_Total_num"] = pd.to_numeric(tri["Score_Total"], errors="coerce")
    tri["Confidence_num"] = pd.to_numeric(tri["Confidence"], errors="coerce")

    fig2 = go.Figure()
    fig2.add_trace(
        go.Scatter(
            x=tri["Confidence_num"],
            y=tri["Score_Total_num"],
            mode="markers+text",
            text=tri["Ticker"].fillna(""),
            textposition="top center",
            marker=dict(size=np.nan_to_num(tri["Revenue_num"] / (np.nanmax(tri["Revenue_num"]) or 1) * 30, nan=10) + 8),
            hovertext=tri["Company"],
            hoverinfo="text",
            name="Targets",
        )
    )
    fig2.update_layout(
        xaxis_title="Confidence (data completeness)",
        yaxis_title="Score_Total",
        height=460,
        margin=dict(l=30, r=30, t=30, b=30),
    )
    st.plotly_chart(fig2, use_container_width=True)
    st.caption("Use this to avoid “high score, low confidence” traps (bubble size ≈ revenue).")


# ----------------------------
# V2 deal logic table (full width)
# ----------------------------
st.markdown("---")
st.subheader("Top targets — V2 Deal Logic")

v2 = scored.copy()
# transparent placeholders
v2["Synergy_NPV_Proxy"] = (v2["Score_Fit"] * 0.25 + v2["Score_Growth"] * 0.15).round(4)

acq_lev = float(acq["NetDebt_EBITDA"]) if pd.notna(acq["NetDebt_EBITDA"]) else 0.0
v2["ProForma_Leverage"] = (acq_lev + (pd.to_numeric(v2["NetDebt_EBITDA"], errors="coerce") / 4.0)).round(4)

v2_cols = ["Company", "Synergy_NPV_Proxy", "ProForma_Leverage", "Score_Total", "Confidence", "Top_Why_Deal_Works"]
st.dataframe(v2[v2_cols].head(15), hide_index=True, use_container_width=True)

st.markdown("---")
st.caption("© 2026 MERJURE. All rights reserved.")
