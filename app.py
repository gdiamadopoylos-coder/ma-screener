import streamlit as st
import pandas as pd
import numpy as np

st.set_page_config(page_title="M&A Target Screener", layout="wide")
st.title("M&A Target Screener (Pilot V1)")
st.caption("Upload a company universe CSV, choose an acquirer, and rank targets with an explainable score.")

# ---------- Upload ----------
uploaded = st.file_uploader("Upload your company universe (CSV or XLSX)", type=["csv", "xlsx"])

if uploaded is None:
    st.info("Upload a CSV to begin. If you don't have one yet, use the template below.")
    template = pd.DataFrame(
        [
            {
                "Company": "AcquirerCo",
                "Ticker": "ACQ",
                "Country": "UK",
                "Sector": "Aerospace & Defence",
                "Revenue": 5000,
                "EBITDA": 900,
                "EBITDA_Margin": 0.18,
                "NetDebt_EBITDA": 2.0,
                "Revenue_Growth_3Y": 0.06,
                "Reg_Risk": 1,          # 1=low, 2=med, 3=high
                "Overlap": 2            # 1=low, 2=med, 3=high
            },
            {
                "Company": "TargetOne",
                "Ticker": "T1",
                "Country": "US",
                "Sector": "Aerospace & Defence",
                "Revenue": 900,
                "EBITDA": 140,
                "EBITDA_Margin": 0.155,
                "NetDebt_EBITDA": 1.5,
                "Revenue_Growth_3Y": 0.08,
                "Reg_Risk": 2,
                "Overlap": 3
            },
            {
                "Company": "TargetTwo",
                "Ticker": "T2",
                "Country": "DE",
                "Sector": "Industrial",
                "Revenue": 700,
                "EBITDA": 80,
                "EBITDA_Margin": 0.114,
                "NetDebt_EBITDA": 3.2,
                "Revenue_Growth_3Y": 0.04,
                "Reg_Risk": 1,
                "Overlap": 1
            },
        ]
    )
    st.download_button(
        "Download CSV template",
        data=template.to_csv(index=False).encode("utf-8"),
        file_name="ma_universe_template.csv",
        mime="text/csv",
    )
    st.stop()

name = uploaded.name.lower()

if name.endswith(".xlsx"):
    xls = pd.ExcelFile(uploaded)
    sheet = "Universe" if "Universe" in xls.sheet_names else xls.sheet_names[0]
    df = pd.read_excel(uploaded, sheet_name=sheet)
else:
    df = pd.read_csv(uploaded)
st.write("Columns detected:", list(df.columns))
# ---------- Validate ----------
required_cols = [
    "Company","Ticker","Country","Sector",
    "Revenue","EBITDA","EBITDA_Margin","NetDebt_EBITDA",
    "Revenue_Growth_3Y","Reg_Risk","Overlap"
]
missing = [c for c in required_cols if c not in df.columns]
if missing:
    st.error(f"Your CSV is missing required columns: {missing}")
    st.stop()

# ---------- Controls ----------
left, right = st.columns([1, 2], gap="large")

with left:
    st.subheader("1) Select acquirer")
    acquirer_name = st.selectbox("Acquirer", df["Company"].unique())

    st.subheader("2) Filters")
    same_sector_only = st.checkbox("Only same sector as acquirer", value=True)
    max_size_pct = st.slider("Max target size (% of acquirer revenue)", 5, 100, 50)
    max_leverage = st.slider("Max target NetDebt/EBITDA", 0.0, 10.0, 5.0, 0.1)

    st.subheader("3) Weights (must sum ~100)")
    w_fit = st.slider("Strategic fit (Overlap)", 0, 100, 30)
    w_growth = st.slider("Growth (Revenue_Growth_3Y)", 0, 100, 20)
    w_margin = st.slider("Margin uplift headroom (EBITDA_Margin)", 0, 100, 25)
    w_risk = st.slider("Risk penalty (Reg_Risk + leverage)", 0, 100, 25)

    st.subheader("4) Horizon scenario")
    horizon = st.selectbox("Horizon", ["2–3 years", "3–5 years", "5–10 years"])
    if horizon == "2–3 years":
        growth_mult = 0.9
        margin_mult = 0.9
    elif horizon == "3–5 years":
        growth_mult = 1.0
        margin_mult = 1.0
    else:
        growth_mult = 1.1
        margin_mult = 1.1

# ---------- Prepare acquirer ----------
acq = df[df["Company"] == acquirer_name].iloc[0]
acq_rev = float(acq["Revenue"])
acq_sector = str(acq["Sector"])

# Exclude acquirer from targets
targets = df[df["Company"] != acquirer_name].copy()

# Apply filters
targets = targets[targets["NetDebt_EBITDA"] <= max_leverage]
targets = targets[targets["Revenue"] <= (max_size_pct / 100.0) * acq_rev]

if same_sector_only:
    targets = targets[targets["Sector"] == acq_sector]

if targets.empty:
    st.warning("No targets match your filters. Relax constraints and try again.")
    st.stop()

# ---------- Scoring helpers ----------
def minmax(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    if s.nunique(dropna=True) <= 1:
        return pd.Series([50.0] * len(s), index=s.index)  # neutral if no variation
    return 100 * (s - s.min()) / (s.max() - s.min())

# Strategic fit: Overlap (1..3) => higher better
fit_score = minmax(targets["Overlap"])

# Growth: apply horizon multiplier
growth_score = minmax(targets["Revenue_Growth_3Y"] * growth_mult)

# Margin: higher margin = (rough) more quality; also apply horizon multiplier
margin_score = minmax(targets["EBITDA_Margin"] * margin_mult)

# Risk: higher reg risk and higher leverage penalize
risk_raw = (targets["Reg_Risk"] * 1.0) + (targets["NetDebt_EBITDA"] * 0.5)
risk_score = 100 - minmax(risk_raw)  # invert: lower risk_raw => higher score

# Normalize weights
w_sum = w_fit + w_growth + w_margin + w_risk
if w_sum == 0:
    w_sum = 1

total = (
    (w_fit / w_sum) * fit_score +
    (w_growth / w_sum) * growth_score +
    (w_margin / w_sum) * margin_score +
    (w_risk / w_sum) * risk_score
)

# ---------- Reasons (rules-based, explainable) ----------
def reason_row(row):
    reasons = []
    if row["Overlap"] >= 3:
        reasons.append("High strategic overlap")
    elif row["Overlap"] == 2:
        reasons.append("Moderate strategic overlap")

    if row["Revenue_Growth_3Y"] >= targets["Revenue_Growth_3Y"].median():
        reasons.append("Above-median growth")

    if row["EBITDA_Margin"] >= targets["EBITDA_Margin"].median():
        reasons.append("Attractive margin profile")

    if row["Reg_Risk"] <= 1:
        reasons.append("Lower regulatory risk")
    if row["NetDebt_EBITDA"] <= targets["NetDebt_EBITDA"].median():
        reasons.append("Conservative leverage")

    if not reasons:
        reasons.append("Meets filter constraints")
    return "; ".join(reasons[:3])

out = targets.copy()
out["Score_Total"] = total.round(1)
out["Score_Fit"] = fit_score.round(1)
out["Score_Growth"] = growth_score.round(1)
out["Score_Margin"] = margin_score.round(1)
out["Score_Risk"] = risk_score.round(1)
out["Top_Reasons"] = out.apply(reason_row, axis=1)

out = out.sort_values("Score_Total", ascending=False)

# -------- V2 DEAL LOGIC RANKING --------
if "Synergy_NPV_Proxy" in df.columns:
    v2 = df.copy()

    v2["Synergy_NPV_Proxy"] = pd.to_numeric(v2["Synergy_NPV_Proxy"], errors="coerce")
    v2["ProForma_Leverage"] = pd.to_numeric(v2["ProForma_Leverage"], errors="coerce")

    # Base score from synergy value
    v2["Score_V2"] = v2["Synergy_NPV_Proxy"] / 100.0

    # Penalize infeasible deals
    v2["Score_V2"] = np.where(
        v2["Feasible"].astype(str).str.upper() == "YES",
        v2["Score_V2"],
        v2["Score_V2"] - 50,
    )

    # Penalize high leverage beyond 3.5x
    v2["Score_V2"] -= np.maximum(0, v2["ProForma_Leverage"] - 3.5) * 10

    v2 = v2.sort_values("Score_V2", ascending=False)

    st.subheader("Top targets — V2 Deal Logic")
    st.dataframe(
        v2[
            [
                "Company",
                "Synergy_NPV_Proxy",
                "ProForma_Leverage",
                "Feasible",
                "Score_V2",
                "Top_Why_Deal_Works",
            ]
        ].head(10),
        use_container_width=True,
    )

with right:
    st.subheader("Ranked targets")
    st.write(f"Acquirer: **{acquirer_name}** | Sector filter: **{same_sector_only}** | Horizon: **{horizon}**")
    st.dataframe(
        out[["Company","Ticker","Country","Sector","Revenue","EBITDA_Margin","NetDebt_EBITDA","Revenue_Growth_3Y",
             "Score_Total","Score_Fit","Score_Growth","Score_Margin","Score_Risk","Top_Reasons"]],
        use_container_width=True,
        hide_index=True
    )

    st.subheader("Top 10")
    st.table(out.head(10)[["Company","Score_Total","Top_Reasons"]])

st.success("Pilot V1 running. Next we’ll improve the scoring logic and add a one-page export.")

