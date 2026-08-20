"""The shared harness for TEK2day's script-style tests.

⚠️ WHY THIS EXISTS — THE SUITE WAS REPORTING SUCCESS IT HAD NOT EARNED.

Thirteen test files each carried their own identical copy of this:

    def check(name, condition, detail=""):
        if condition:
            _passed += 1
        else:
            _failed.append(...)          # <- records, and RETURNS

It never raised. Those files are standalone scripts whose `main()` inspects the
failure list at the end and sets the exit code — correct when run as
`python test_eps.py`, and completely blind under pytest, which never calls
`main()`. It just runs the `test_*` functions, and a function whose every
assertion is false raises nothing, so pytest records a PASS.

Demonstrated 19 Aug 2026: a throwaway test asserting `2 + 2 == 5` reported
"1 passed" under pytest.

So `pytest -q` saying "254 passed" meant only "nothing crashed". A wrong ANSWER
in any of those 571 assertions was invisible. That number was quoted as evidence
of safety repeatedly during the price-outage work; it was weaker than it looked.

`check` now RAISES, so a false assertion fails in both harnesses. `run_all`
keeps the standalone behaviour that made the original design worth having: every
test function still runs, and every failure is reported at the end, rather than
the first one hiding the rest.

⚠️ ONE COPY. The duplication is what let one file drift (test_partner_symbols
installed its stubs inside `main()`, so under pytest it ran with no stubs at all
and failed 13 of 14 — while passing 126 of 126 when run properly). Do not
reintroduce a per-file `check`.
"""

_passed = 0
_failed = []


def reset():
    """Start a fresh tally. Called by run_all; harmless to call directly."""
    global _passed
    _passed = 0
    _failed.clear()


def check(name, condition, detail=""):
    """Record a passing assertion, or RAISE on a failing one.

    Raising is the entire point: pytest discovers failures through exceptions,
    and the old version returned quietly. `run_all` catches these so a
    standalone run still reports every failure rather than stopping at the first.
    """
    global _passed
    if condition:
        _passed += 1
        return True
    message = f"{name}{(' - ' + detail) if detail else ''}"
    _failed.append(message)
    raise AssertionError(message)


def run_all(namespace, setup=None):
    """Run every test_* function in `namespace`. Returns a process exit code.

    Each function is isolated: a failure inside one does not stop the others, so
    a standalone run still tells you everything that is broken in one pass —
    which is what the original hand-rolled harness got right.
    """
    reset()
    if setup is not None:
        setup()

    broken = []
    for name, fn in sorted(namespace.items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        try:
            fn()
        except AssertionError as exc:
            broken.append(f"{name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - a crash is a failure, not a stop
            broken.append(f"{name}: {type(exc).__name__}: {exc}")

    total = _passed + len(broken)
    if broken:
        print(f"FAILED {len(broken)}/{total}")
        for item in broken:
            print(f"  - {item}")
        return 1
    print(f"{_passed}/{total} tests pass")
    return 0
