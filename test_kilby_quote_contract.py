"""TEK2day's quote still fits Kilby's checker (#9).

scripts/check_kilby_quote_contract.py runs a sample TEK2day quote through Kilby's own
verifier. It went stale for five weeks (Kilby's 2026-09-06 change) because nothing ran
it. This runs it with the suite whenever a Kilby tree is available: KILBY_ROOT, or the
chatllm checkout beside this repo. GitHub's runner has no Kilby checkout, so there it
is skipped and says so.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _kilby_root():
    candidates = [os.environ.get("KILBY_ROOT"), HERE.parent / "chatllm"]
    for candidate in candidates:
        if candidate and (Path(candidate) / "kilby_backend" / "report_evidence.py").is_file():
            return Path(candidate)
    return None


@pytest.mark.skipif(_kilby_root() is None, reason="no Kilby tree here (set KILBY_ROOT to run the cross-check)")
def test_tek2day_quote_passes_kilbys_checker():
    run = subprocess.run(
        [sys.executable, str(HERE / "scripts" / "check_kilby_quote_contract.py"), "--kilby-root", str(_kilby_root())],
        capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "PASS: actual TEK2day producer -> JSON -> actual Kilby verifier" in run.stdout
    assert run.stdout.count("PASS: rejects") == 5
