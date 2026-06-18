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


def _us_date(s) -> str:
    """YYYY-MM-DD -> MM-DD-YYYY (leave anything else as-is)."""
    try:
        y, m, d = str(s).split("-")[:3]
        return f"{m}-{d}-{y}"
    except Exception:
        return str(s or "")


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


# statement key -> (Firestore section, terminal field-list attr, sheet/title)
_FIN_STATEMENTS = {
    "income": ("income", "INCOME_FIELDS", "Income Statement"),
    "balance": ("balance_sheet", "BALANCE_FIELDS", "Balance Sheet"),
    "cashflow": ("cash_flow", "CASHFLOW_FIELDS", "Cash Flow"),
}


@router.get("/api/export/financials")
def export_financials(request: Request, symbol: str = Query(...),
                      statement: str = Query("income"), fmt: str = Query("xlsx")):
    auth.require_uid(request)  # LOGIN-GATED (free account for now)
    symbol = symbol.upper().strip()
    spec = _FIN_STATEMENTS.get(statement)
    if not spec:
        raise HTTPException(status_code=400, detail="Unknown statement.")
    section, fields_attr, title = spec
    fields = getattr(terminal, fields_attr)
    all_fins = terminal._all_financials(symbol)
    if not all_fins:
        raise HTTPException(status_code=404, detail="No financials for that symbol.")

    # Build the same period groups the UI uses.
    groups = []
    if section == "balance_sheet":
        seen, unique = set(), []
        for f in sorted(all_fins, key=lambda f: f.get("period_end", "")):
            pe = f.get("period_end", "")
            if pe in seen:
                continue
            seen.add(pe)
            unique.append(f)
        if unique:
            groups.append(("Recent Periods", unique[-8:]))
    else:
        q = sorted([f for f in all_fins if f.get("freq") != "FY"], key=lambda f: f.get("period_end", ""))[-4:]
        a = sorted([f for f in all_fins if f.get("freq") == "FY"], key=lambda f: f.get("period_end", ""))[-4:]
        if q:
            groups.append(("Quarterly", q))
        if a:
            groups.append(("Annual", a))

    out_sections = []
    for gtitle, periods in groups:
        cols = [str(p.get("period") or p.get("period_end", "")) for p in periods]
        rows = []
        for field in fields:
            key, label = field if isinstance(field, tuple) else (field, field)
            vals = [terminal._to_float(p.get(section, {}).get(key)) for p in periods]
            if not any(v is not None for v in vals):
                continue
            # Statement line items in $ millions; EPS rows are plain decimals.
            fk = "num2" if "EPS" in label else "dollar_m"
            rows.append({"label": label, "values": vals, "fmt": fk})
        if rows:
            out_sections.append({"title": gtitle, "corner": "Line Item", "columns": cols, "rows": rows})
    if not out_sections:
        raise HTTPException(status_code=404, detail="No financial data.")

    data = excel_export.build_workbook(
        f"{symbol} — {title}  |  figures in $ millions (EPS in $)",
        "Source: TEK2day",
        out_sections, sheet_name=title[:31])
    return _xlsx_response(data, _safe(f"{symbol}_{title}") + ".xlsx")


@router.get("/api/export/estimates")
def export_estimates(request: Request, symbol: str = Query(...), fmt: str = Query("xlsx")):
    auth.require_uid(request)  # LOGIN-GATED (free account for now)
    symbol = symbol.upper().strip()
    history = terminal._estimate_history(symbol)
    if not history:
        raise HTTPException(status_code=404, detail="No estimates for that symbol.")
    d = history[0]

    out_sections = []
    for prefix, title in [("eps", "EPS Estimates"), ("rev", "Revenue Estimates")]:
        metric_map = {k[len(prefix) + 1:]: v for k, v in d.items() if k.startswith(prefix + "_")}
        if not metric_map:
            continue
        sample = next(iter(metric_map.values()))
        codes = list(sample.keys()) if isinstance(sample, dict) else []
        periods = [p for p in terminal.PERIOD_ORDER if p in codes] + [p for p in codes if p not in terminal.PERIOD_ORDER]
        order = terminal.METRIC_ORDER_REV if prefix == "rev" else terminal.METRIC_ORDER_EPS
        rows = []
        for mk in order:
            if mk not in metric_map:
                continue
            vals = [terminal._to_float(metric_map[mk].get(p)) for p in periods]
            if mk == "growth":
                fk = "pct"
            elif mk == "numberofanalysts":
                fk = "int"
            elif prefix == "rev" and mk in ("avg", "high", "low", "yearagorevenue"):
                fk = "dollar_m"
            elif prefix == "eps" and mk in ("avg", "high", "low", "yearagoeps"):
                fk = "price"
            else:
                fk = "num2"
            rows.append({"label": terminal.METRIC_LABELS.get(mk, mk), "values": vals, "fmt": fk})
        if rows:
            out_sections.append({
                "title": title, "corner": "Metric",
                "columns": [terminal.PERIOD_LABELS.get(p, p) for p in periods], "rows": rows,
            })
    if not out_sections:
        raise HTTPException(status_code=404, detail="No estimate data.")

    data = excel_export.build_workbook(
        f"{symbol} — Estimates  |  as of {_us_date(d.get('date'))}",
        "Source: Yahoo Finance, TEK2day",
        out_sections, sheet_name="Estimates")
    return _xlsx_response(data, _safe(f"{symbol}_estimates") + ".xlsx")
