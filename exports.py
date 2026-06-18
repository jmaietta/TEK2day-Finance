"""Data-table exports for TEK2day Finance — LOGIN-GATED (free account for now;
may move behind a paywall later). Builds formatted .xlsx workbooks from the same
market data the UI shows, via the read-only excel_export engine. No data writes.

Reuses terminal._market_snapshot for comp (raw numeric values), so Excel cells hold
real numbers with Excel number formats — sortable, precise — not pre-formatted text."""
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

import auth
import excel_export
import terminal

router = APIRouter()

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx_response(data: bytes, filename: str) -> Response:
    return Response(
        content=data,
        media_type=_XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _safe(s: str) -> str:
    return "".join(c if (c.isalnum() or c == "-") else "_" for c in (s or "")).strip("_") or "export"


# Comp rows mirror the live comp table: (label, snapshot key, number-format key).
# Big-dollar magnitudes shown in $ billions (his call); ratios as Nx; price as $.
_COMP_ROWS = [
    ("Price", "price", "price"),
    ("Market Cap", "market_cap", "dollar_b"),
    ("EV", "enterprise_value", "dollar_b"),
    ("Revenue (TTM)", "revenue", "dollar_b"),
    ("EBITDA (TTM)", "ebitda", "dollar_b"),
    ("Net Income (TTM)", "net_income", "dollar_b"),
    ("EPS (TTM)", "eps_ttm", "num2"),
    ("EPS (Fwd)", "forward_eps", "num2"),
    ("P/E TTM (GAAP)", "pe_ttm", "ratio"),
    ("Fwd P/E (Est)", "forward_pe", "ratio"),
    ("P/S (TTM)", "ps_ttm", "ratio"),
    ("EV/Rev (TTM)", "ev_revenue", "ratio"),
    ("EV/EBITDA (TTM)", "ev_ebitda", "ratio"),
    ("EV/OpCF (TTM)", "ev_opcf", "ratio"),
    ("EV/FCF (TTM)", "ev_fcf", "ratio"),
]


@router.get("/api/export/compare")
def export_compare(request: Request, symbols: str = Query(...), fmt: str = Query("xlsx")):
    auth.require_uid(request)  # LOGIN-GATED (free account for now)
    syms = [s.upper().strip() for s in symbols.split(",") if s.strip()][:6]
    snaps = []
    for s in syms:
        snap = terminal._market_snapshot(s)
        if snap:
            snaps.append(snap)
    if not snaps:
        raise HTTPException(status_code=404, detail="No data for those symbols.")
    cols = [s["symbol"] for s in snaps]
    rows = [{"label": "Company", "values": [s.get("name", s["symbol"]) for s in snaps], "fmt": "text"}]
    for label, key, fmtkey in _COMP_ROWS:
        rows.append({"label": label, "values": [s.get(key) for s in snaps], "fmt": fmtkey})
    data = excel_export.build_workbook(
        "",  # no title text — logo only
        "Source: Yahoo Finance, TEK2day",  # rendered BELOW the table
        [{"corner": "Metric", "columns": cols, "rows": rows}],
        sheet_name="Comparison",
    )
    return _xlsx_response(data, _safe("_".join(cols)) + "_comparison.xlsx")
