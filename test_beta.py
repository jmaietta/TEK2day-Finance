#!/usr/bin/env python3
"""Tests for beta: Yahoo's own, never one we calculate.

    pytest test_beta.py

No network, no Firestore. Decided 30 Oct 2026: /ticker showed N/A for every
company because the Beta row read a saved copy nothing ever saved, while the
page's own Yahoo profile carried beta.
"""
from testkit import check

import fetchers
import terminal


def _info(monkeypatch, info):
    class T:
        def __init__(self, symbol):
            self.info = info
    monkeypatch.setattr(fetchers.yf, "Ticker", T)
    return fetchers.fetch_ticker_info("KO")


def test_the_weekly_profile_keeps_yahoos_beta(monkeypatch):
    meta = _info(monkeypatch, {"symbol": "KO", "shortName": "Coca-Cola", "beta": 0.43})
    check("beta", meta["beta"] == 0.43, str(meta))
    check("source", meta["beta_source"] == "yahoo")
    check("dated", len(meta["beta_as_of"]) == 10)


def test_no_beta_from_yahoo_keeps_the_last_one(monkeypatch):
    """The record is merged, so a key left out keeps what is stored."""
    for missing in ({}, {"beta": None}, {"beta": float("nan")}, {"beta": True}):
        meta = _info(monkeypatch, {"symbol": "KO", "shortName": "Coca-Cola", **missing})
        check(f"no beta key for {missing}", not {"beta", "beta_source", "beta_as_of"} & set(meta), str(meta))


# ── the one-request fetch for the one-off fill ───────────────────────────────

def _answer(monkeypatch, result, asked=None):
    from yfinance import data

    def get_raw_json(self, url, params=None, timeout=30):
        if asked is not None:
            asked.append((url, params))
        return {"quoteSummary": {"result": [result], "error": None}}
    monkeypatch.setattr(data.YfData, "get_raw_json", get_raw_json)


def test_one_request_brings_yahoos_beta(monkeypatch):
    asked = []
    _answer(monkeypatch, {"quoteType": {"symbol": "NVDA"}, "summaryDetail": {"beta": 2.12}}, asked)
    got = fetchers.fetch_beta("NVDA")
    check("beta", got["beta"] == 2.12 and got["beta_source"] == "yahoo", str(got))
    check("one request", len(asked) == 1, str(asked))
    check("asks only the sections it needs", "summaryDetail" in asked[0][1]["modules"]
          and "quoteType" in asked[0][1]["modules"], str(asked))


def test_beta_from_the_other_section_when_needed(monkeypatch):
    _answer(monkeypatch, {"quoteType": {"symbol": "BRK-B"}, "summaryDetail": {},
                          "defaultKeyStatistics": {"beta": 0.87}})
    check("key statistics", fetchers.fetch_beta("BRK-B")["beta"] == 0.87)


def test_no_beta_is_left_blank_not_made_up(monkeypatch):
    _answer(monkeypatch, {"quoteType": {"symbol": "NEWCO"}, "summaryDetail": {"beta": None}})
    check("blank", fetchers.fetch_beta("NEWCO") == {})


def test_an_answer_about_another_company_is_refused(monkeypatch):
    _answer(monkeypatch, {"quoteType": {"symbol": "META"}, "summaryDetail": {"beta": 1.2}})
    try:
        fetchers.fetch_beta("FB")
        refused = False
    except ValueError:
        refused = True
    check("refused", refused)


# ── /ticker ─────────────────────────────────────────────────────────────────

def test_ticker_shows_the_beta_the_page_already_gets(monkeypatch):
    monkeypatch.setattr(terminal, "_yahoo", lambda s: {"beta": 1.27})
    check("live", terminal._beta("AAPL", {"beta": 1.19}) == 1.27)


def test_ticker_falls_back_to_the_saved_beta(monkeypatch):
    monkeypatch.setattr(terminal, "_yahoo", lambda s: {})
    check("saved", terminal._beta("AAPL", {"beta": 1.19}) == 1.19)
    check("none", terminal._beta("AAPL", {}) is None)
    monkeypatch.setattr(terminal, "_yahoo", lambda s: {"beta": float("nan")})
    check("NaN is none", terminal._beta("AAPL", {}) is None)
