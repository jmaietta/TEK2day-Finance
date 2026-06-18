"""TEK2day Finance — Excel (.xlsx) export engine.

READ-ONLY by construction: this module only turns already-fetched data into a
formatted spreadsheet. It imports no Firestore / storage write or delete
functions and performs no data-store writes of any kind.

Produces a branded, print-formatted workbook (logo + title header, real Excel
number formats, landscape fit-to-width) for the comp / financials / estimates
tables. Callers pass RAW numeric values plus a per-row format key; the numbers
stay precise and sortable in the cell while these formats control display only.
"""
from __future__ import annotations

import io
import math
import os
from datetime import datetime, timezone

import xlsxwriter

_HERE = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(_HERE, "static", "icon-512.png")

# Excel number-format strings. Trailing commas scale the displayed value
# (one comma = /1,000), so a raw dollar figure can be shown in $M or $B while
# the true value remains in the cell.
NUM_FORMATS = {
    "text":     None,
    "price":    '"$"#,##0.00',          # $254.50
    "dollar_b": '"$"#,##0.0,,,"B"',     # raw dollars shown in billions: $3,200.5B
    "dollar_m": '"$"#,##0,,"M"',        # raw dollars shown in millions: $391,035M
    "ratio":    '0.0"x"',               # 41.5x
    "pct":      '0.00%',                # fraction 0.0235 -> 2.35%
    "num2":     '#,##0.00',             # 6.13
    "int":      '#,##0',                # 1,234
}

_HDR_BG = "#1C1B14"
_SECTION_BG = "#F2EFE6"


def _fmt_factory(wb):
    """Return a cached getter for xlsxwriter formats keyed by their props."""
    cache = {}

    def get(num_key="text", *, bold=False, align="right", bg=None, color=None, border=1):
        key = (num_key, bold, align, bg, color, border)
        if key not in cache:
            props = {"border": border, "align": align, "valign": "vcenter"}
            nf = NUM_FORMATS.get(num_key)
            if nf:
                props["num_format"] = nf
            if bold:
                props["bold"] = True
            if bg:
                props["bg_color"] = bg
            if color:
                props["font_color"] = color
            cache[key] = wb.add_format(props)
        return cache[key]

    return get


def _is_blank(v):
    return v is None or (isinstance(v, float) and math.isnan(v))


def build_workbook(report_title, subtitle, sections, *, sheet_name="Export", landscape=True):
    """Build an .xlsx and return its bytes.

    sections: list of dicts, each rendered as a labelled matrix block:
      {
        "title":   optional section heading (str),
        "corner":  top-left header cell text (str),
        "columns": [column header, ...],
        "rows":    [{"label": str, "values": [...], "fmt": "<NUM_FORMATS key>"}, ...],
      }
    Values should be RAW numbers (or strings for fmt="text"); None/NaN -> blank.
    """
    bio = io.BytesIO()
    wb = xlsxwriter.Workbook(bio, {"in_memory": True})
    get = _fmt_factory(wb)
    ws = wb.add_worksheet(sheet_name[:31])

    ncols = max((1 + len(s.get("columns", [])) for s in sections), default=2)

    # --- header: full-width title + subtitle (logo omitted for now; re-add cleanly
    #     once the data/format is approved — it was overlapping the title) ---
    title_fmt = wb.add_format({"bold": True, "font_size": 15, "font_color": "#B97A14", "valign": "vcenter"})
    sub_fmt = wb.add_format({"font_size": 9, "font_color": "#666666", "valign": "vcenter"})
    ws.merge_range(0, 0, 0, ncols - 1, report_title, title_fmt)
    ws.merge_range(1, 0, 1, ncols - 1, subtitle, sub_fmt)
    ws.set_column(0, 0, 30)
    ws.set_column(1, ncols - 1, 16)

    row = 3
    first_header_row = None
    for s in sections:
        if s.get("title"):
            ws.merge_range(row, 0, row, ncols - 1, s["title"],
                           get("text", bold=True, align="left", bg=_SECTION_BG, border=0))
            row += 1
        # column-header row
        ws.write(row, 0, s.get("corner", ""),
                 get("text", bold=True, align="left", bg=_HDR_BG, color="#FFFFFF"))
        for j, c in enumerate(s.get("columns", [])):
            ws.write(row, 1 + j, c, get("text", bold=True, align="center", bg=_HDR_BG, color="#FFFFFF"))
        if first_header_row is None:
            first_header_row = row
        row += 1
        # data rows
        for r in s.get("rows", []):
            ws.write(row, 0, r.get("label", ""), get("text", align="left"))
            cell_fmt = get(r.get("fmt", "num2"))
            for j, v in enumerate(r.get("values", [])):
                if _is_blank(v):
                    ws.write_blank(row, 1 + j, None, cell_fmt)
                elif isinstance(v, (int, float)):
                    ws.write_number(row, 1 + j, v, cell_fmt)
                else:
                    ws.write(row, 1 + j, v, get("text", align="left"))
            row += 1
        row += 1  # gap between sections

    # --- print setup ---
    if landscape:
        ws.set_landscape()
    ws.set_paper(1)               # US Letter
    ws.fit_to_pages(1, 0)         # 1 page wide, unlimited tall
    ws.set_margins(0.4, 0.4, 0.55, 0.55)
    ws.center_horizontally()
    if first_header_row is not None:
        ws.repeat_rows(first_header_row)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ws.set_footer(f"&LTEK2day Finance&R{stamp}   Page &P of &N")

    wb.close()
    bio.seek(0)
    return bio.getvalue()


def _write_sample():
    """Generate throwaway sample files for visual review (Phase 0)."""
    comp = [{
        "corner": "Metric",
        "columns": ["AAPL", "MSFT", "NVDA"],
        "rows": [
            {"label": "Company", "values": ["Apple Inc.", "Microsoft Corp.", "NVIDIA Corp."], "fmt": "text"},
            {"label": "Price", "values": [254.50, 415.20, 1180.0], "fmt": "price"},
            {"label": "Market Cap", "values": [3.2e12, 3.1e12, 2.9e12], "fmt": "dollar_b"},
            {"label": "Enterprise Value", "values": [3.18e12, 3.0e12, 2.88e12], "fmt": "dollar_b"},
            {"label": "Revenue", "values": [391.035e9, 245.1e9, 60.9e9], "fmt": "dollar_b"},
            {"label": "Net Income", "values": [93.736e9, 88.1e9, 29.8e9], "fmt": "dollar_b"},
            {"label": "EPS (TTM)", "values": [6.13, 11.8, 1.19], "fmt": "num2"},
            {"label": "P/E TTM", "values": [41.5, 35.2, None], "fmt": "ratio"},
            {"label": "EV/EBITDA", "values": [28.4, 24.1, 55.2], "fmt": "ratio"},
            {"label": "Div Yield", "values": [0.0044, 0.0072, 0.0003], "fmt": "pct"},
            {"label": "Beta", "values": [1.24, 0.91, 1.68], "fmt": "num2"},
        ],
    }]
    fin = [
        {"title": "Quarterly", "corner": "Line Item",
         "columns": ["Q4 2024", "Q3 2024", "Q2 2024", "Q1 2024"],
         "rows": [
            {"label": "Total Revenue", "values": [124.3e9, 94.9e9, 85.8e9, 90.8e9], "fmt": "dollar_m"},
            {"label": "Gross Profit", "values": [58.3e9, 43.9e9, 39.7e9, 42.3e9], "fmt": "dollar_m"},
            {"label": "Operating Income", "values": [42.8e9, 29.6e9, 25.4e9, 27.9e9], "fmt": "dollar_m"},
            {"label": "Net Income", "values": [36.3e9, 14.7e9, 21.4e9, 23.6e9], "fmt": "dollar_m"},
            {"label": "Diluted EPS", "values": [2.40, 0.97, 1.40, 1.53], "fmt": "num2"},
         ]},
        {"title": "Annual", "corner": "Line Item",
         "columns": ["FY 2024", "FY 2023", "FY 2022"],
         "rows": [
            {"label": "Total Revenue", "values": [391.035e9, 383.285e9, 394.328e9], "fmt": "dollar_m"},
            {"label": "Net Income", "values": [93.736e9, 96.995e9, 99.803e9], "fmt": "dollar_m"},
            {"label": "Diluted EPS", "values": [6.08, 6.13, 6.11], "fmt": "num2"},
         ]},
    ]
    _DESK = os.path.dirname(_HERE)
    out1 = os.path.join(_DESK, "comp_format_test.xlsx")
    with open(out1, "wb") as f:
        f.write(build_workbook(
            "TEK2day Finance — Comparison",
            "AAPL  ·  MSFT  ·  NVDA      |      as of 2026-06-17      |      Source: Yahoo Finance, TEK2day",
            comp, sheet_name="Comparison"))
    out2 = os.path.join(_DESK, "financials_format_test.xlsx")
    with open(out2, "wb") as f:
        f.write(build_workbook(
            "TEK2day Finance — Income Statement (AAPL)",
            "Apple Inc. (AAPL)      |      figures in $ millions (EPS in $)      |      Source: TEK2day Firestore",
            fin, sheet_name="Income Statement"))
    print("wrote:", out1)
    print("wrote:", out2)


if __name__ == "__main__":
    _write_sample()
