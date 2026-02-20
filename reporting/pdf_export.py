# reporting/pdf_export.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Dict, Optional, List, Tuple

import pandas as pd

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader


# =========================
# Styling / constants
# =========================

PAGE_W, PAGE_H = A4

# Print area
MARGIN_L = 2.0 * cm
MARGIN_R = 2.0 * cm
MARGIN_T = 1.6 * cm
MARGIN_B = 1.6 * cm

CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R

# Colors (banker-clean, low saturation)
C_TEXT = colors.HexColor("#111827")     # near-black
C_MUTED = colors.HexColor("#6B7280")    # slate/gray
C_LINE = colors.HexColor("#D1D5DB")     # light gray
C_BOX = colors.HexColor("#F3F4F6")      # very light gray
C_HEADER = colors.HexColor("#0F172A")   # deep slate
C_ACCENT = colors.HexColor("#111827")   # keep accent subtle

FONT = "Helvetica"
FONT_B = "Helvetica-Bold"


# =========================
# Helpers
# =========================

def _fmt_float(x: object, ndp: int = 2, fallback: str = "") -> str:
    try:
        if x is None:
            return fallback
        v = float(x)
        return f"{v:.{ndp}f}"
    except Exception:
        return fallback


def _fmt_int(x: object, fallback: str = "") -> str:
    try:
        if x is None:
            return fallback
        return str(int(round(float(x))))
    except Exception:
        return fallback


def _clip(s: str, n: int) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[: max(0, n - 1)].rstrip() + "…"


def _wrap_lines(c: Canvas, text: str, font: str, size: int, max_w: float) -> List[str]:
    """
    Word-wrap into multiple lines so that each line width <= max_w.
    """
    text = "" if text is None else str(text)
    words = text.split()
    if not words:
        return [""]

    c.setFont(font, size)
    lines: List[str] = []
    cur = words[0]
    for w in words[1:]:
        candidate = cur + " " + w
        if c.stringWidth(candidate, font, size) <= max_w:
            cur = candidate
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _draw_box(c: Canvas, x: float, y_top: float, w: float, h: float,
              fill=C_BOX, stroke=C_LINE, stroke_w: float = 0.8):
    """
    Draw a box whose top-left is (x, y_top). ReportLab uses bottom-left origin.
    """
    y = y_top - h
    c.setLineWidth(stroke_w)
    c.setStrokeColor(stroke)
    c.setFillColor(fill)
    c.rect(x, y, w, h, fill=1, stroke=1)


def _draw_text_block(
    c: Canvas,
    x: float,
    y_top: float,
    w: float,
    title: Optional[str],
    bullets: Optional[List[str]] = None,
    paragraph: Optional[str] = None,
    font_size: int = 10,
    line_gap: float = 3.0,
    pad: float = 10.0,
) -> float:
    """
    Draw a boxed text block. Returns used height.
    Height auto-expands to fit content.
    """
    # Estimate lines
    title_h = 0
    lines: List[str] = []
    if title:
        title_h = (font_size + 2) + line_gap

    if paragraph:
        lines += _wrap_lines(c, paragraph, FONT, font_size, w - 2 * pad)

    if bullets:
        for b in bullets:
            wrapped = _wrap_lines(c, "• " + b, FONT, font_size, w - 2 * pad)
            lines += wrapped

    if not lines:
        lines = [""]

    line_h = font_size + line_gap
    content_h = len(lines) * line_h
    total_h = pad + title_h + content_h + pad

    _draw_box(c, x, y_top, w, total_h)

    y = y_top - pad
    if title:
        c.setFillColor(C_TEXT)
        c.setFont(FONT_B, font_size + 1)
        c.drawString(x + pad, y - (font_size + 1), title)
        y -= title_h

    c.setFont(FONT, font_size)
    c.setFillColor(C_TEXT)
    yy = y
    for ln in lines:
        c.drawString(x + pad, yy - font_size, ln)
        yy -= line_h

    return total_h


def _draw_header(
    c: Canvas,
    title: str,
    subtitle: str,
    page_num: int,
    logo_path: Optional[str] = None,
):
    """
    Clean header: title left, subtitle under it.
    Logo right, ABOVE the header separator line.
    """
    # Title
    c.setFillColor(C_HEADER)
    c.setFont(FONT_B, 18)
    c.drawString(MARGIN_L, PAGE_H - MARGIN_T, title)

    # Subtitle
    c.setFillColor(C_MUTED)
    c.setFont(FONT, 9)
    c.drawString(MARGIN_L, PAGE_H - MARGIN_T - 18, subtitle)

    # Logo (top-right), positioned so it never touches the separator line
    logo_w = 3.0 * cm
    logo_h = 1.2 * cm
    logo_x = PAGE_W - MARGIN_R - logo_w
    logo_y_top = PAGE_H - 1.05 * cm  # higher up than before

    if logo_path:
        try:
            img = ImageReader(logo_path)
            c.drawImage(img, logo_x, logo_y_top - logo_h, logo_w, logo_h,
                        preserveAspectRatio=True, mask='auto', anchor='n')
        except Exception:
            # fallback: outlined placeholder
            c.setStrokeColor(C_LINE)
            c.setFillColor(colors.white)
            c.rect(logo_x, logo_y_top - logo_h, logo_w, logo_h, fill=1, stroke=1)
            c.setFillColor(C_MUTED)
            c.setFont(FONT_B, 9)
            c.drawCentredString(logo_x + logo_w / 2, logo_y_top - logo_h / 2 - 3, "LOGO")

    # Separator line (lower than logo)
    line_y = PAGE_H - MARGIN_T - 28
    c.setStrokeColor(C_LINE)
    c.setLineWidth(1)
    c.line(MARGIN_L, line_y, PAGE_W - MARGIN_R, line_y)

    # Footer page number ONLY (bottom-right)
    c.setFillColor(C_MUTED)
    c.setFont(FONT, 9)
    c.drawRightString(PAGE_W - MARGIN_R, MARGIN_B - 0.5 * cm, str(page_num))


def _safe_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _normalize_top5(df: pd.DataFrame) -> pd.DataFrame:
    """
    Return top 5 with required columns and safe fallbacks.
    """
    company = _safe_col(df, ["Company", "company"])
    country = _safe_col(df, ["Country", "country", "Ctry", "ctr"])
    revenue = _safe_col(df, ["Revenue", "revenue"])
    ebitda_m = _safe_col(df, ["EBITDA_Margin", "EBITDA%", "EBITDA %", "ebitda_margin"])
    nd_ebitda = _safe_col(df, ["NetDebt_EBITDA", "ND/EBITDA", "netdebt_ebitda"])
    score = _safe_col(df, ["Score_Total", "score_total", "Score_V2", "Score_V2", "Score", "score"])

    out = pd.DataFrame()
    out["Company"] = df[company] if company else ""
    out["Country"] = df[country] if country else ""
    out["Revenue"] = df[revenue] if revenue else None
    out["EBITDA_Margin"] = df[ebitda_m] if ebitda_m else None
    out["NetDebt_EBITDA"] = df[nd_ebitda] if nd_ebitda else None
    out["Score"] = df[score] if score else None

    out = out.head(5).copy()
    return out


def _draw_kpi_row(
    c: Canvas,
    y_top: float,
    kpis: List[Tuple[str, str]],
) -> float:
    """
    Draw 4 KPI boxes in a row. Returns height used.
    """
    box_h = 1.55 * cm
    gap = 0.35 * cm
    n = len(kpis)
    box_w = (CONTENT_W - gap * (n - 1)) / n

    x = MARGIN_L
    for label, value in kpis:
        _draw_box(c, x, y_top, box_w, box_h, fill=colors.white, stroke=C_LINE, stroke_w=0.8)
        c.setFillColor(C_MUTED)
        c.setFont(FONT, 8)
        c.drawString(x + 8, y_top - 10, label)

        c.setFillColor(C_TEXT)
        c.setFont(FONT_B, 14)
        c.drawString(x + 8, y_top - 30, value)

        x += box_w + gap

    return box_h


def _fit_col_widths(c: Canvas, headers: List[str], rows: List[List[str]], max_w: float,
                    font: str = FONT, size: int = 9, pad: float = 10.0) -> List[float]:
    """
    Compute column widths that fit within max_w, with sensible caps.
    """
    c.setFont(font, size)

    # initial widths from max header/data width
    widths = []
    for j, h in enumerate(headers):
        w = c.stringWidth(h, font, size)
        for r in rows:
            if j < len(r):
                w = max(w, c.stringWidth(str(r[j]), font, size))
        widths.append(w + pad)

    # Cap Company column, reserve chart space elsewhere (we handle separately)
    # We’ll allow company to be largest, but still constrained.
    if len(widths) >= 2:
        widths[1] = min(widths[1], 9.5 * cm)  # Company
    # Country narrow
    if len(widths) >= 3:
        widths[2] = min(widths[2], 1.6 * cm)

    total = sum(widths)
    if total <= max_w:
        return widths

    # scale down proportionally (but keep some min widths)
    mins = [1.1 * cm] * len(widths)
    if len(widths) >= 2:
        mins[1] = 5.5 * cm  # Company minimum
    if len(widths) >= 3:
        mins[2] = 1.2 * cm  # Country minimum

    # If even mins don't fit, force scale mins too (rare on A4, but safe)
    if sum(mins) > max_w:
        scale = max_w / sum(mins)
        return [m * scale for m in mins]

    # distribute remaining after mins
    remaining = max_w - sum(mins)
    extras = [max(0.0, w - m) for w, m in zip(widths, mins)]
    extra_total = sum(extras) if sum(extras) > 0 else 1.0
    final = [m + remaining * (e / extra_total) for m, e in zip(mins, extras)]
    return final


def _draw_table(
    c: Canvas,
    x: float,
    y_top: float,
    headers: List[str],
    rows: List[List[str]],
    max_w: float,
) -> float:
    """
    Draw a table that fits max_w. Returns height used.
    """
    font_size = 9
    row_h = 0.62 * cm
    header_h = 0.70 * cm
    pad_x = 6

    widths = _fit_col_widths(c, headers, rows, max_w=max_w, font=FONT, size=font_size, pad=14)

    # Outer border
    total_h = header_h + row_h * len(rows)
    _draw_box(c, x, y_top, sum(widths), total_h, fill=colors.white, stroke=C_LINE, stroke_w=0.9)

    # Header background
    c.setFillColor(C_HEADER)
    c.rect(x, y_top - header_h, sum(widths), header_h, fill=1, stroke=0)

    # Header text
    c.setFillColor(colors.white)
    c.setFont(FONT_B, font_size)
    cx = x
    for j, h in enumerate(headers):
        c.drawString(cx + pad_x, y_top - header_h + 0.22 * cm, h)
        cx += widths[j]

    # Rows
    c.setFont(FONT, font_size)
    y = y_top - header_h
    for i, r in enumerate(rows):
        # zebra
        if i % 2 == 0:
            c.setFillColor(colors.HexColor("#FAFAFA"))
            c.rect(x, y - row_h, sum(widths), row_h, fill=1, stroke=0)

        # row line
        c.setStrokeColor(C_LINE)
        c.setLineWidth(0.6)
        c.line(x, y - row_h, x + sum(widths), y - row_h)

        # text
        c.setFillColor(C_TEXT)
        cx = x
        for j, cell in enumerate(r):
            cell = "" if cell is None else str(cell)

            # Company wraps if needed (column 1)
            if j == 1:
                max_cell_w = widths[j] - 2 * pad_x
                lines = _wrap_lines(c, cell, FONT, font_size, max_cell_w)
                lines = lines[:2]  # max 2 lines (banker clean)
                # vertically center within row
                base_y = y - 0.18 * cm
                if len(lines) == 1:
                    c.drawString(cx + pad_x, base_y, lines[0])
                else:
                    c.drawString(cx + pad_x, base_y + 0.18 * cm, lines[0])
                    c.drawString(cx + pad_x, base_y - 0.18 * cm, lines[1])
            else:
                c.drawString(cx + pad_x, y - 0.42 * cm, _clip(cell, 24))

            cx += widths[j]
        y -= row_h

    # vertical grid lines
    c.setStrokeColor(C_LINE)
    c.setLineWidth(0.6)
    cx = x
    for w in widths[:-1]:
        cx += w
        c.line(cx, y_top, cx, y_top - total_h)

    return total_h


def _draw_bar_chart(
    c: Canvas,
    x: float,
    y_top: float,
    w: float,
    h: float,
    labels: List[str],
    values: List[float],
    title: str,
    subtitle: str,
) -> float:
    """
    Horizontal bar chart with wrapped labels and safe right padding.
    """
    _draw_box(c, x, y_top, w, h, fill=colors.white, stroke=C_LINE, stroke_w=0.9)

    pad = 10
    c.setFillColor(C_TEXT)
    c.setFont(FONT_B, 10)
    c.drawString(x + pad, y_top - 14, title)

    c.setFillColor(C_MUTED)
    c.setFont(FONT, 8)
    c.drawString(x + pad, y_top - 28, subtitle)

    # layout
    chart_top = y_top - 40
    chart_bottom = y_top - h + 12
    chart_h = chart_top - chart_bottom

    n = max(1, len(labels))
    bar_gap = 6
    bar_h = max(10, (chart_h - (n - 1) * bar_gap) / n)

    # Reserve left for labels
    label_w = min(6.0 * cm, w * 0.52)
    bar_x0 = x + pad + label_w + 8
    bar_x1 = x + w - pad - 18  # right padding so numbers never clip

    # scale
    vmax = max([abs(v) for v in values] + [1.0])
    # allow negative bars: baseline at 0 in middle of bar area
    zero_x = bar_x0 + (bar_x1 - bar_x0) * 0.45  # bias slightly left
    pos_span = bar_x1 - zero_x
    neg_span = zero_x - bar_x0

    y = chart_top - bar_h
    for lab, val in zip(labels, values):
        # label (wrap to 2 lines max)
        c.setFillColor(C_TEXT)
        c.setFont(FONT, 8)
        lines = _wrap_lines(c, lab, FONT, 8, label_w)
        lines = lines[:2]
        if len(lines) == 1:
            c.drawString(x + pad, y + bar_h / 2 - 3, lines[0])
        else:
            c.drawString(x + pad, y + bar_h / 2 + 3, lines[0])
            c.drawString(x + pad, y + bar_h / 2 - 9, lines[1])

        # bar
        c.setFillColor(colors.HexColor("#CBD5E1"))
        if val >= 0:
            bw = pos_span * (val / vmax)
            c.rect(zero_x, y + 2, bw, bar_h - 4, fill=1, stroke=0)
            # value
            c.setFillColor(C_TEXT)
            c.setFont(FONT_B, 8)
            c.drawRightString(bar_x1 + 10, y + bar_h / 2 - 3, _fmt_float(val, 1))
        else:
            bw = neg_span * (abs(val) / vmax)
            c.rect(zero_x - bw, y + 2, bw, bar_h - 4, fill=1, stroke=0)
            c.setFillColor(C_TEXT)
            c.setFont(FONT_B, 8)
            c.drawRightString(bar_x1 + 10, y + bar_h / 2 - 3, _fmt_float(val, 1))

        y -= (bar_h + bar_gap)

    return h


# =========================
# Public builder
# =========================

def build_screener_pack_pdf(
    out_df: pd.DataFrame,
    acquirer_name: str,
    sector_filter: str,
    horizon: str,
    filters: Dict[str, str],
    weights: Dict[str, int],
    title: str = "M&A Target Screening Pack",
    logo_path: Optional[str] = None,
) -> bytes:
    """
    Banker-style 2-page PDF bytes:
      Page 1: Decision memo + Top 5 + Score chart
      Page 2: Scenario / Filters / Weights + audit trail + sanity checks
    """
    df = out_df.copy()

    # Determine score column for ranking
    score_col = _safe_col(df, ["Score_Total", "score_total", "Score_V2", "Score_V2", "Score", "score"])
    if score_col:
        df = df.sort_values(score_col, ascending=False, na_position="last")
    else:
        score_col = "Score_Total"
        df[score_col] = 0.0

    top5 = _normalize_top5(df)

    # KPIs
    targets_ranked = len(df)
    top5_listed = len(top5)

    median_nd = float(pd.to_numeric(top5["NetDebt_EBITDA"], errors="coerce").median()) if top5_listed else 0.0
    median_margin = float(pd.to_numeric(top5["EBITDA_Margin"], errors="coerce").median()) if top5_listed else 0.0

    # Recommendation pick
    rec_company = str(top5.iloc[0]["Company"]) if top5_listed else "N/A"
    rec_score = float(pd.to_numeric(top5.iloc[0]["Score"], errors="coerce")) if top5_listed else 0.0

    # Memo executive paragraph (scope + what this is)
    scope_para = (
        "This pack summarizes a first-pass, model-driven screen of potential M&A targets under the selected scenario. "
        "Targets are filtered for feasibility (sector, size, leverage) and ranked by a weighted score intended to proxy: "
        "strategic fit, growth, margin uplift potential, and risk/leverage tolerance. "
        "Outputs are directional and must be validated with company-specific diligence and market comps."
    )

    # Executive bullets (tight and decision-led)
    bullets = [
        f"Recommendation: prioritize **{rec_company}** under the current scenario (rank #1; Score = {_fmt_float(rec_score, 1)}).",
        f"Top-5 median ND/EBITDA = {_fmt_float(median_nd, 2)}x (constraint = {filters.get('max_netdebt_ebitda', 'N/A')}x).",
        f"Top-5 median EBITDA margin = {_fmt_float(median_margin, 2)} (proxy from input dataset).",
    ]

    # Prepare table rows
    table_headers = ["Rank", "Company", "Ctry", "Revenue", "EBITDA %", "ND/EBITDA", "Score"]
    rows: List[List[str]] = []
    for i in range(top5_listed):
        r = top5.iloc[i]
        rows.append([
            str(i + 1),
            str(r["Company"]),
            _clip(str(r["Country"]), 3),
            _fmt_int(r["Revenue"]),
            _fmt_float(r["EBITDA_Margin"], 2),
            _fmt_float(r["NetDebt_EBITDA"], 2),
            _fmt_float(r["Score"], 1),
        ])

    # Chart
    chart_labels = [str(x) for x in top5["Company"].tolist()] if top5_listed else []
    chart_values = [float(pd.to_numeric(x, errors="coerce") or 0.0) for x in top5["Score"].tolist()] if top5_listed else []

    # Render PDF
    buf = BytesIO()
    c = Canvas(buf, pagesize=A4)

    # -------------------------
    # Page 1: Decision memo
    # -------------------------
    subtitle = (
        f"Acquirer: {acquirer_name}  |  Sector filter: {sector_filter}  |  Horizon: {horizon}  |  "
        f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
    )
    _draw_header(c, title, subtitle, page_num=1, logo_path=logo_path)

    y = PAGE_H - MARGIN_T - 40  # below header line

    # Section title
    c.setFillColor(C_TEXT)
    c.setFont(FONT_B, 12)
    c.drawString(MARGIN_L, y, "Decision memo — Executive summary")
    y -= 10

    # Memo scope paragraph (small box)
    used = _draw_text_block(
        c,
        x=MARGIN_L,
        y_top=y,
        w=CONTENT_W,
        title="Executive Summary (scope)",
        paragraph=scope_para,
        bullets=None,
        font_size=9,
        pad=10,
    )
    y -= used + 10

    # Decision bullets (auto-sized box)
    used = _draw_text_block(
        c,
        x=MARGIN_L,
        y_top=y,
        w=CONTENT_W,
        title="Key points",
        bullets=[b.replace("**", "") for b in bullets],
        paragraph=None,
        font_size=10,
        pad=10,
    )
    y -= used + 10

    # KPI row
    kpis = [
        ("Targets ranked", _fmt_int(targets_ranked)),
        ("Top-5 listed", _fmt_int(top5_listed)),
        ("Top-5 median ND/EBITDA", f"{_fmt_float(median_nd, 2)}x"),
        ("Top-5 median EBITDA %", _fmt_float(median_margin, 2)),
    ]
    used = _draw_kpi_row(c, y, kpis)
    y -= used + 14

    # Table + chart block
    c.setFillColor(C_TEXT)
    c.setFont(FONT_B, 11)
    c.drawString(MARGIN_L, y, "Top 5 targets (ranked)")
    y -= 8

    # split area: table left, chart right (ensure nothing clips)
    gap = 0.6 * cm
    table_w = CONTENT_W * 0.62
    chart_w = CONTENT_W - table_w - gap

    table_h = _draw_table(
        c,
        x=MARGIN_L,
        y_top=y,
        headers=table_headers,
        rows=rows,
        max_w=table_w,
    )

    _draw_bar_chart(
        c,
        x=MARGIN_L + table_w + gap,
        y_top=y,
        w=chart_w,
        h=table_h,
        labels=chart_labels,
        values=chart_values,
        title="Score (Top 5)",
        subtitle="Score = weighted composite ranking metric (higher = better under scenario)",
    )

    # Explain score (short note under)
    note_y = y - table_h - 10
    c.setFillColor(C_MUTED)
    c.setFont(FONT, 8)
    c.drawString(
        MARGIN_L,
        note_y,
        "Note: Score is a scenario-specific, weighted composite (strategic fit, growth, margin uplift, risk penalty). "
        "It is not a valuation and must be validated with comps and diligence.",
    )

    c.showPage()

    # -------------------------
    # Page 2: Audit trail
    # -------------------------
    subtitle2 = f"Scenario / filters / weights (audit trail)  |  Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
    _draw_header(c, title, subtitle2, page_num=2, logo_path=logo_path)

    y = PAGE_H - MARGIN_T - 40

    c.setFillColor(C_TEXT)
    c.setFont(FONT_B, 12)
    c.drawString(MARGIN_L, y, "Scenario, filters & weights (audit trail)")
    y -= 12

    # Two boxes side-by-side: Filters + Weights
    gap = 0.8 * cm
    box_w = (CONTENT_W - gap) / 2
    box_h = 4.0 * cm

    # Filters box
    _draw_box(c, MARGIN_L, y, box_w, box_h, fill=colors.white, stroke=C_LINE, stroke_w=0.9)
    c.setFillColor(C_TEXT)
    c.setFont(FONT_B, 10)
    c.drawString(MARGIN_L + 10, y - 14, "Filters")

    c.setFont(FONT, 9)
    c.setFillColor(C_TEXT)

    fx = MARGIN_L + 10
    fy = y - 30
    frows = [
        ("Only same sector", filters.get("only_same_sector", "")),
        ("Max target size (% rev)", filters.get("max_target_size_pct", "")),
        ("Max ND/EBITDA", filters.get("max_netdebt_ebitda", "")),
        ("Horizon", horizon),
        ("Sector filter", sector_filter),
    ]
    for k, v in frows:
        c.setFillColor(C_MUTED)
        c.drawString(fx, fy, k)
        c.setFillColor(C_TEXT)
        c.drawRightString(MARGIN_L + box_w - 10, fy, str(v))
        fy -= 14

    # Weights box
    wx0 = MARGIN_L + box_w + gap
    _draw_box(c, wx0, y, box_w, box_h, fill=colors.white, stroke=C_LINE, stroke_w=0.9)
    c.setFillColor(C_TEXT)
    c.setFont(FONT_B, 10)
    c.drawString(wx0 + 10, y - 14, "Weights (sum ≈ 100)")

    wy = y - 30
    total_w = 0
    for key in ["strategic_fit", "growth", "margin_uplift", "risk_penalty"]:
        label = {
            "strategic_fit": "Strategic fit",
            "growth": "Growth",
            "margin_uplift": "Margin uplift",
            "risk_penalty": "Risk penalty",
        }[key]
        val = int(weights.get(key, 0))
        total_w += val
        c.setFillColor(C_MUTED)
        c.setFont(FONT, 9)
        c.drawString(wx0 + 10, wy, label)
        c.setFillColor(C_TEXT)
        c.drawRightString(wx0 + box_w - 10, wy, str(val))
        wy -= 14

    c.setFillColor(C_MUTED)
    c.drawString(wx0 + 10, wy - 2, "Total")
    c.setFillColor(C_TEXT)
    c.drawRightString(wx0 + box_w - 10, wy - 2, str(total_w))

    y -= box_h + 14

    # Methodology (tight)
    method_bullets = [
        "Targets are screened using feasibility filters (sector, size, leverage) and ranked by a weighted score.",
        "Score is a composite proxy for strategic fit, growth and margin uplift potential, adjusted for risk and leverage constraints.",
        "Output is indicative; results depend on input data quality, normalization choices and scenario assumptions.",
    ]
    used = _draw_text_block(
        c,
        x=MARGIN_L,
        y_top=y,
        w=CONTENT_W,
        title="Methodology (summary)",
        bullets=method_bullets,
        paragraph=None,
        font_size=9,
        pad=10,
    )
    y -= used + 10

    # Sanity checks / data quality flags (dynamic)
    flags = []
    if any(v < 0 for v in chart_values):
        flags.append("Some scores are negative. Confirm normalization ranges and penalty calibration.")
    if top5_listed < 5:
        flags.append("Fewer than 5 targets meet constraints under current scenario.")
    if not flags:
        flags.append("No automatic flags triggered. Still validate inputs and run sensitivity checks.")

    used = _draw_text_block(
        c,
        x=MARGIN_L,
        y_top=y,
        w=CONTENT_W,
        title="Comp sanity checks & data quality flags",
        bullets=flags,
        paragraph=None,
        font_size=9,
        pad=10,
    )
    y -= used + 6

    c.showPage()
    c.save()

    return buf.getvalue()
