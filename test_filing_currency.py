#!/usr/bin/env python3
"""Statements are labelled in the currency they are filed in.

    pytest test_filing_currency.py

No network, no Firestore.

WHY THIS FILE EXISTS — TSM, 18 October 2026.

TSM trades in US dollars in New York and files in Taiwan dollars. Its Q2 2026
revenue is stored as 1,270,381,000,000 — NT$ — and every surface labelled it
"$M", which reads as $1.27T, about 30x too big. Of 10,029 active companies, 699
file in a currency other than the US dollar (SEC filings, 2025q4-2026q3).

Nothing is converted. The figures are as filed; only the label changes, and an
unknown currency keeps today's "$" label rather than a guess.
"""
import json

from testkit import check

import envelope
import fetchers
import partner_api
import terminal

# TSM-shaped: NT$ revenue, NT$ EPS per ADR.
TSM_RECORDS = [
    {"period": "2026-Q2", "period_end": "2026-06-30", "freq": "Q",
     "income": {"Total Revenue": 1_270_381_000_000.0, "Diluted EPS": 136.231667},
     "balance_sheet": {"Total Assets": 8_000_000_000_000.0},
     "cash_flow": {"Operating Cash Flow": 700_000_000_000.0}},
]


class Req:
    headers: dict = {}


def install(financial_currency):
    import storage
    meta = {"symbol": "TSM", "name": "Taiwan Semiconductor", "active": True, "currency": "USD"}
    if financial_currency is not None:
        meta["financial_currency"] = financial_currency
    storage.get_ticker_meta = lambda s: meta
    terminal._firestore_meta = lambda s: meta
    terminal._all_financials = lambda s: TSM_RECORDS
    partner_api.require_kilby = lambda r: "test"


def api(statement="income"):
    result = partner_api.equity_financials(Req(), symbol="TSM", statement=statement,
                                           frequency="quarterly")
    return json.loads(result.body) if hasattr(result, "body") else result


# ── what counts as a currency ────────────────────────────────────────────────

def test_only_a_plain_three_letter_code_is_a_filing_currency():
    check("TWD kept", envelope.filing_currency("TWD") == "TWD")
    check("spaces trimmed", envelope.filing_currency(" EUR ") == "EUR")
    for bad in (None, "", "US", "USDX", "usd", "GBp", 978):
        check(f"{bad!r} is unknown", envelope.filing_currency(bad) is None)


# ── the weekly pull keeps it, from the response it already gets ─────────────

class _Ticker:
    def __init__(self, info):
        self.info = info


def test_the_weekly_pull_keeps_yahoos_financial_currency(monkeypatch):
    info = {"symbol": "TSM", "shortName": "Taiwan Semiconductor", "currency": "USD",
            "financialCurrency": "TWD", "sector": "Technology", "industry": "Semiconductors"}
    monkeypatch.setattr(fetchers.yf, "Ticker", lambda s: _Ticker(info))
    meta = fetchers.fetch_ticker_info("TSM")
    check("filing currency kept", meta["financial_currency"] == "TWD", meta)
    check("quote currency untouched", meta["currency"] == "USD", meta)


def test_a_response_without_it_cannot_erase_a_stored_one(monkeypatch):
    """The write merges, so a missing key leaves the stored value alone."""
    info = {"symbol": "TSM", "shortName": "Taiwan Semiconductor", "currency": "USD"}
    monkeypatch.setattr(fetchers.yf, "Ticker", lambda s: _Ticker(info))
    meta = fetchers.fetch_ticker_info("TSM")
    check("no key written", "financial_currency" not in meta, meta)


# ── the shared label ─────────────────────────────────────────────────────────

def test_us_dollars_and_unknown_read_exactly_as_before():
    cells = [("Total Revenue", 90e9)]
    check("unknown", terminal.statement_scale("income", cells)[0] == "$M")
    check("USD", terminal.statement_scale("income", cells, "USD")[0] == "$M")
    check("small USD", terminal.statement_scale("income", [("Total Revenue", 5e5)], "USD")[0] == "$K")
    check("EPS unchanged", terminal.statement_display("income", "Diluted EPS", 1.08, 1e6, 0, "USD") == "$1.08")


def test_a_foreign_filer_is_labelled_by_its_code_not_a_dollar_sign():
    cells = [("Total Revenue", 1.27e12)]
    check("TWD millions", terminal.statement_scale("income", cells, "TWD")[0] == "TWD M")
    check("TWD thousands", terminal.statement_scale("income", [("Total Revenue", 5e5)], "TWD")[0] == "TWD K")
    check("EPS in TWD", terminal.statement_display("income", "Diluted EPS", 136.231667, 1e6, 0, "TWD")
          == "TWD 136.23")
    check("a loss keeps its brackets", terminal._eps(-1.5, "DKK") == "(DKK 1.50)")
    check("figures are not converted",
          terminal.statement_display("income", "Total Revenue", 1_270_381_000_000.0, 1e6, 0, "TWD")
          == "1,270,381")


def test_revenue_estimates_follow_the_filing_currency():
    check("TWD", terminal._dollar(1_459_139_238_150.0, "TWD") == "TWD 1.46T")
    check("USD unchanged", terminal._dollar(90e9) == "$90.00B")


# ── the feed Kilby reads ─────────────────────────────────────────────────────

def test_kilby_gets_the_filing_currency_in_the_label_and_the_units_block():
    install("TWD")
    body = api()
    check("unit label", body["data"]["unit"] == "TWD M", body["data"]["unit"])
    check("units block", body["units"]["currency"] == "TWD", body["units"])
    eps = next(r for r in body["data"]["rows"] if r["field"] == "Diluted EPS")
    check("EPS display", eps["display"][0] == "TWD 136.23", eps["display"])
    rev = next(r for r in body["data"]["rows"] if r["field"] == "Total Revenue")
    check("raw value as filed", rev["values"][0] == 1_270_381_000_000.0)


def test_an_unknown_currency_answers_as_every_response_did_before():
    install(None)
    body = api()
    check("unit label", body["data"]["unit"] == "$M", body["data"]["unit"])
    check("units block", body["units"]["currency"] == "USD", body["units"])


# ── the website ──────────────────────────────────────────────────────────────

def test_the_website_card_states_the_filing_currency():
    import app
    install("TWD")
    payload = app._financial_payload.__wrapped__ if hasattr(app._financial_payload, "__wrapped__") else app._financial_payload
    out = payload("TSM", "income", terminal.INCOME_FIELDS, "Income Statement")
    check("unit", out["unit"] == "TWD M", out["unit"])
    eps = next(r for s in out["sections"] for r in s["rows"] if r["label"] == "Reported EPS (Diluted)")
    check("EPS", eps["values"][0] == "TWD 136.23", eps["values"])


def test_the_public_company_page_states_the_filing_currency():
    import seo_pages
    table = seo_pages._income_table(TSM_RECORDS, "TWD")
    eps = next(r for r in table["rows"] if r["label"] == "Diluted EPS")
    check("EPS", eps["values"][0] == "TWD 136.23", eps["values"])
    us = seo_pages._income_table(TSM_RECORDS, None)
    eps = next(r for r in us["rows"] if r["label"] == "Diluted EPS")
    check("unknown unchanged", eps["values"][0] == terminal._price(136.231667), eps["values"])
