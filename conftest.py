"""Shared test setup.

Several test files swap a module's functions for fakes by plain assignment
(``terminal._firestore_meta = lambda s: ...``) and never put them back, so a
later test read the fake instead of the real function: 20 tests passed alone and
failed in the full run (owner, 2026-10-03: "We r tooo fucking accepting of
failed tests."). After every test, these modules get their original attributes back.
"""
import importlib

import pytest

_SHARED = ("storage", "terminal", "partner_api", "beta", "volatility")


@pytest.fixture(autouse=True)
def _restore_shared_modules():
    modules = []
    for name in _SHARED:
        try:
            modules.append(importlib.import_module(name))
        except Exception:
            continue
    saved = [(m, dict(vars(m))) for m in modules]
    yield
    for module, original in saved:
        current = vars(module)
        for key in [k for k in current if k not in original]:
            delattr(module, key)
        for key, value in original.items():
            if current.get(key) is not value:
                setattr(module, key, value)


@pytest.fixture(autouse=True)
def _no_beta_reads(monkeypatch):
    """beta.for_symbol reads Firestore. Every test gets a blank beta unless it
    puts its own in, and no industry stand-in; test_beta.py tests the real
    ones against fakes."""
    import beta
    monkeypatch.setattr(beta, "for_symbol", lambda symbol, meta=None: dict.fromkeys(beta.FIELDS))
    monkeypatch.setattr(beta, "for_symbols", lambda symbols, metas: {s: dict.fromkeys(beta.FIELDS) for s in symbols})
    monkeypatch.setattr(beta, "industry_standin", lambda industry, window_end=None: {
        "industry_beta_median": None, "industry_beta_peers": 0, "industry_beta_industry": industry or None,
        "industry_beta_reason": None})


@pytest.fixture(autouse=True)
def _no_volatility_reads(monkeypatch):
    """volatility.for_symbols reads Firestore and every price write leaves a
    volatility mark. Every test gets a blank figure and no mark unless it puts
    its own in; test_volatility.py tests the real ones against fakes."""
    import volatility
    monkeypatch.setattr(volatility, "for_symbols",
                        lambda symbols, metas: {s: dict.fromkeys(volatility.FIELDS) for s in symbols})
    monkeypatch.setattr(volatility, "mark", lambda symbol, oldest: True)
