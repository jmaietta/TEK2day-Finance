"""Run each test file in a fresh process with external data access blocked.

Legacy suites replace module globals without restoring them, so combining them
in one pytest process does not test the repository's actual implementations.
"""
import argparse
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def child(test_file):
    sys.path.insert(0, str(ROOT))
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    import socket
    import requests
    import storage
    import yfinance
    import pytest
    import traceback

    attempts = []

    def blocked(*args, **kwargs):
        caller = traceback.extract_stack(limit=2)[0]
        attempts.append(f"{Path(caller.filename).name}:{caller.lineno} ({caller.name})")
        raise AssertionError("External access forbidden in offline regressions")

    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
    requests.sessions.Session.request = blocked
    storage.get_db = blocked
    yfinance.Ticker = blocked  # also blocks curl-backed Yahoo network access
    result = pytest.main([str(test_file), "-q", "-p", "no:cacheprovider", "--tb=short"])
    if attempts:
        print(f"FAIL: {len(attempts)} external access attempt(s), including swallowed errors")
        print("Call sites: " + ", ".join(sorted(set(attempts))))
        return 1
    return result


def main():
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT,
                        help="Explicit source tree, e.g. an untouched baseline worktree")
    parser.add_argument("files", nargs="*", help="Default: all root test_*.py files")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    ROOT = args.repo_root.resolve()
    files = [ROOT / name for name in args.files] if args.files else sorted(ROOT.glob("test_*.py"))
    for path in files:
        if path.resolve().parent != ROOT or not path.is_file() or not path.name.startswith("test_"):
            parser.error("Only existing root test files are accepted")
    if args.child:
        if len(files) != 1:
            parser.error("Child mode requires exactly one file")
        return child(files[0])
    failures = []
    for path in files:
        print(f"\n{path.name}", flush=True)
        result = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve()),
             "--repo-root", str(ROOT), "--child", path.name],
            cwd=ROOT, check=False,
        )
        if result.returncode:
            failures.append(path.name)
    print(f"\n{len(files) - len(failures)}/{len(files)} test files passed", flush=True)
    if failures:
        print("Failed: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
