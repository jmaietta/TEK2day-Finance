"""GET /partner/v1/symbols: the whole security master, for Kilby (and later CEORater).

Firestore and auth are stubbed with monkeypatch, so nothing leaks into other
test modules and nothing touches production.
"""
import json

import pytest
from fastapi import HTTPException

import partner_api
import storage


class _Doc:
    def __init__(self, key, data):
        self.id, self._data = key, data

    def to_dict(self):
        return dict(self._data)


class _DB:
    def __init__(self, docs):
        self.docs, self.reads = docs, 0

    def collection(self, name):
        assert name == "tickers"
        db = self

        class _Col:
            def select(self, fields):
                return self

            def stream(self):
                db.reads += 1
                return [_Doc(k, v) for k, v in db.docs.items()]
        return _Col()


class _Request:
    def __init__(self, headers=None):
        self.headers = headers or {}


DOCS = {
    "NVDA": {"name": "NVIDIA Corporation", "cik": 1045810, "exchange": "NMS", "sector": "Technology",
             "industry": "Semiconductors", "active": True},
    "NEWCO": {"name": "NEWCO INC", "cik": 2000001, "sec_exchange": "Nasdaq", "sector": "", "industry": "",
              "active": True, "added_at": "2026-10-05T21:00:00+00:00"},
    "DEAD": {"name": "Dead Co", "active": False, "deactivated_reason": "no_price_30d",
             "deactivated_at": "2026-10-05T21:00:00+00:00"},
    "OLDNAME": {"name": "Old Name", "active": False, "renamed_to": "NEWNAME"},
}


@pytest.fixture
def db(monkeypatch):
    fake = _DB(DOCS)
    monkeypatch.setattr(storage, "get_db", lambda: fake)
    monkeypatch.setattr(partner_api, "require_kilby", lambda request: "kilby@example.com")
    monkeypatch.setattr(partner_api, "_master_cache", {})
    return fake


def _body(response):
    return json.loads(response.body)


def test_every_known_ticker_with_identity_sector_and_status(db):
    response = partner_api.symbol_master(_Request())
    body = _body(response)
    assert body["dataset"] == "symbol_master"
    rows = {r["symbol"]: r for r in body["data"]["symbols"]}
    assert set(rows) == {"DEAD", "NEWCO", "NVDA"}            # the retired alias is left out
    assert rows["NVDA"] == {"symbol": "NVDA", "name": "NVIDIA Corporation", "cik": 1045810, "exchange": "NMS",
                            "sector": "Technology", "industry": "Semiconductors", "sector_source": None, "active": True,
                            "added_at": None, "deactivated_at": None, "deactivated_reason": None}
    assert rows["NEWCO"]["exchange"] == "Nasdaq" and rows["NEWCO"]["sector"] is None   # blank until the weekly refresh
    assert rows["DEAD"]["active"] is False and rows["DEAD"]["deactivated_reason"] == "no_price_30d"
    assert body["data"]["counts"] == {"total": 3, "active": 2, "inactive": 1}
    assert [r["symbol"] for r in body["data"]["symbols"]] == ["DEAD", "NEWCO", "NVDA"]


def test_a_caller_with_the_current_version_gets_304_and_one_read_serves_many(db):
    first = partner_api.symbol_master(_Request())
    etag = first.headers["etag"]
    again = partner_api.symbol_master(_Request({"if-none-match": etag}))
    assert again.status_code == 304 and again.body == b""
    stale = partner_api.symbol_master(_Request({"if-none-match": '"something-else"'}))
    assert stale.status_code == 200
    assert db.reads == 1                                     # cached for the TTL


def test_only_kilby_may_call(monkeypatch, db):
    def refuse(request):
        raise HTTPException(status_code=401, detail="no")
    monkeypatch.setattr(partner_api, "require_kilby", refuse)
    with pytest.raises(HTTPException) as exc:
        partner_api.symbol_master(_Request())
    assert exc.value.status_code == 401


def test_an_unreadable_store_is_503_not_an_empty_master(monkeypatch, db):
    monkeypatch.setattr(storage, "get_db", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(HTTPException) as exc:
        partner_api.symbol_master(_Request())
    assert exc.value.status_code == 503
