#!/usr/bin/env python3
"""Share counts are the one exception to write-once.

    python test_share_counts.py

No network, no Firestore.

WHY — 18 August 2026, HIS INSTRUCTION: "If Yahoo sends a new share count due to
a split, reverse merger, etc., if share count changes we have to reflect that
new share count in TEK2day Finance."

Financial records are write-once: ingestion fills empty fields and never
overwrites, because Yahoo's own history regresses and a refresh that overwrote
would replace real figures with nothing. That guard is right for everything
EXCEPT share counts, which legitimately CHANGE. A split does not make the stored
count older, it makes it WRONG — and Yahoo rewrites every past period onto the
new basis when one happens.

MEASURED across 150 companies: 59 of 430 stored counts (13.7%, spanning 53
companies) no longer matched Yahoo. GIPR was stored at exactly 10x Yahoo across
four consecutive quarters — a 1-for-10 reverse split ingested either side of,
leaving the whole series on the pre-split basis and every per-share figure out
by a factor of ten. CP, ALK, RJF and CSL showed the milder version at 2-5%.

Frozen counts are worse than merely stale: prices ARE refreshed daily on a
split-adjusted basis, so the two silently contradict each other.

⚠️ THE GUARD ON THE EXCEPTION — also his: overwrite ONLY when the incoming value
is finite AND non-zero, so a blank or a zero can never wipe a real count.
"""
import sys

import storage

_passed = 0
_failed = []


def check(name, condition, detail=""):
    global _passed
    if condition:
        _passed += 1
    else:
        _failed.append(f"{name}{(' — ' + detail) if detail else ''}")


def doc(**income):
    return {"income": dict(income)}


def test_a_changed_share_count_is_taken():
    """GIPR: a 1-for-10 reverse split we ingested either side of."""
    merged, filled = storage.merge_financial_doc(
        doc(**{"Diluted Average Shares": 5_443_188.0}),
        doc(**{"Diluted Average Shares": 544_318.0}))
    check("count updated", merged["income"]["Diluted Average Shares"] == 544_318.0,
          str(merged["income"]["Diluted Average Shares"]))
    check("reported as filled", "income.Diluted Average Shares" in filled, str(filled))


def test_everything_else_keeps_the_write_once_guard():
    """Yahoo's history regresses — BRK.B 2024-12-31 now returns 2 of 48
    cash-flow fields against a stored copy holding a real $4,621m operating cash
    flow. Only share counts are exempt."""
    merged, filled = storage.merge_financial_doc(
        doc(**{"Total Revenue": 100.0, "Net Income": -5.0}),
        doc(**{"Total Revenue": 999.0, "Net Income": 999.0}))
    check("revenue protected", merged["income"]["Total Revenue"] == 100.0)
    check("net income protected", merged["income"]["Net Income"] == -5.0)
    check("nothing filled", filled == [], str(filled))


def test_a_blank_never_wipes_a_real_count():
    for label, incoming in (("None", None), ("nan", float("nan")), ("zero", 0.0)):
        merged, filled = storage.merge_financial_doc(
            doc(**{"Diluted Average Shares": 5_443_188.0}),
            doc(**{"Diluted Average Shares": incoming}))
        check(f"{label} refused",
              merged["income"]["Diluted Average Shares"] == 5_443_188.0,
              str(merged["income"]["Diluted Average Shares"]))
        check(f"{label} not reported as filled", filled == [], str(filled))


def test_an_identical_count_is_not_reported_as_a_change():
    """Otherwise every run would look like it repaired something."""
    merged, filled = storage.merge_financial_doc(
        doc(**{"Diluted Average Shares": 544_318.0}),
        doc(**{"Diluted Average Shares": 544_318.0}))
    check("no spurious fill", filled == [], str(filled))


def test_an_empty_count_is_still_filled_normally():
    merged, filled = storage.merge_financial_doc(
        doc(**{"Diluted Average Shares": None}),
        doc(**{"Diluted Average Shares": 544_318.0}))
    check("gap filled", merged["income"]["Diluted Average Shares"] == 544_318.0)


def test_every_share_field_flexes():
    for field in storage.SHARE_COUNT_FIELDS:
        merged, _f = storage.merge_financial_doc(doc(**{field: 100.0}), doc(**{field: 10.0}))
        check(f"{field} flexes", merged["income"][field] == 10.0,
              str(merged["income"][field]))


def test_the_exempt_list_is_only_share_counts():
    """A wider list would quietly reopen the write-once hole."""
    check("nothing but shares",
          all("Shares" in f or "Share" in f for f in storage.SHARE_COUNT_FIELDS),
          str(sorted(storage.SHARE_COUNT_FIELDS)))


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    total = _passed + len(_failed)
    if _failed:
        print(f"FAILED {len(_failed)}/{total}")
        for f in _failed:
            print(f"  - {f}")
        return 1
    print(f"{_passed}/{total} tests pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
