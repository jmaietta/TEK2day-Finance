"""Shared test setup.

Several test files swap a module's functions for fakes by plain assignment
(``terminal._firestore_meta = lambda s: ...``) and never put them back, so a
later test read the fake instead of the real function: 20 tests passed alone and
failed in the full run (owner, 2026-10-03: "We r tooo fucking accepting of
failed tests."). After every test, these modules get their original attributes back.
"""
import importlib

import pytest

_SHARED = ("storage", "terminal", "partner_api")


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
