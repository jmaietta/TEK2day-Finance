"""Bounded read-only pre/post verification of the approved June SEC repair.

Capture every financial descendant; compare direct price/estimate observations
and metadata separately. Private snapshots never belong in Git.
"""
import argparse
from datetime import datetime, timezone
from itertools import islice
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.inspect_ticker_identity import capture
from scripts.run_sec_repair import read, save
from sec_maintenance import checked_binding, commit_candidate, candidate_writes, make_plan, run_fallback
from security_identity import digest, require, RetiredSymbol

APPROVED = "1d2d3401552330e6c2ef12645e44482a8d4765ea3f9eb47e5869c208565eecd1"


def snapshot(s):
    return {"exists": s.exists, "data": s.to_dict(), "update_time": s.update_time}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--before", type=Path)
    args = parser.parse_args()
    require(not args.output.exists() and not args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]),
            "Use a new private receipt path")
    plan = read(args.plan)
    require(digest(plan) == APPROVED and plan["symbol"] == "BNY", "Wrong approved repair")
    token = subprocess.run(["gcloud.cmd", "auth", "print-access-token", "--account=jmaietta@ceorater.com"],
                           check=True, capture_output=True, text=True).stdout.strip()
    from google.cloud import firestore
    from google.oauth2.credentials import Credentials
    db = firestore.Client(project="yfinance-cli", database="(default)", credentials=Credentials(token))
    _, root, state = checked_binding(db, "BNY")
    require(digest(state) == digest(plan["route"]), "Reviewed route changed")
    refs = list(islice(root.collection("financials").list_documents(page_size=40), 41))
    require(len(refs) <= 40, "Financial period bound reached")
    tree = capture(db, roots=sorted({r.path for r in refs} | {plan["path"]}), max_documents=250, max_depth=8)
    protected = {p: snapshot(db.document(p).get()) for p in
                 (root.path, plan["route_path"], "tickers/BK", "tickers/BNY")}
    datasets = {}
    for name in ("prices", "estimates"):
        rows = list(root.collection(name).limit(2001).stream(timeout=30))
        require(len(rows) <= 2000, "Dataset bound reached")
        datasets[name] = {s.reference.path: snapshot(s) for s in rows}
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "plan_sha256": APPROVED,
              "financial_tree": tree, "protected_metadata": protected, "datasets": datasets,
              "period_count": len(refs), "financial_record_count": len(tree["records"]),
              "dataset_counts": {k: len(v) for k, v in datasets.items()}}
    if args.before:
        before = read(args.before)
        require(before["plan_sha256"] == APPROVED, "Wrong baseline")
        require(digest(protected) == digest(before["protected_metadata"]), "Metadata or route changed")
        require(digest(datasets) == digest(before["datasets"]), "Prices or estimates changed")
        audit_path = plan["write_templates"][-1]["path"]
        audit_record = tree["records"][audit_path]
        audit = audit_record["data"]
        original = plan["tree"][plan["path"]]
        expected = candidate_writes(make_plan(plan["path"], original["data"], plan["candidate"]),
                                    plan["route"], original["update_time"], audit["recorded_at"], approval_sha256=APPROVED)
        for write in expected:
            require(digest(tree["records"][write["path"]]["data"]) == digest(write["data"]), "Applied payload mismatch")
        require(tree["records"][plan["path"]]["update_time"] == audit_record["update_time"], "Writes were not atomic")
        actual_unchanged = {p: d for p, d in tree["records"].items() if p not in {plan["path"], audit_path}}
        old_unchanged = {p: d for p, d in before["financial_tree"]["records"].items() if p != plan["path"]}
        require(digest(actual_unchanged) == digest(old_unchanged), "Other financial records or descendants changed")
        require(tree["collections"] == sorted(set(before["financial_tree"]["collections"]) |
                                              {plan["path"] + "/identity_observations"}), "Unexpected nested collection change")
        api, commits = db._firestore_api, []
        real_commit = api.commit
        def no_writes(request=None, **kwargs):
            require(isinstance(request, dict) and not request.get("writes"), "Nonempty verification commit blocked")
            commits.append(0)
            return real_commit(request=request, **kwargs)
        import storage
        storage._db = db
        with patch.object(api, "commit", side_effect=no_writes):
            require(commit_candidate(db, "BNY", plan["candidate"], apply=True, approval=plan)["action"] == "noop",
                    "Approved repair rerun was not a no-op")
            current = tree["records"][plan["path"]]["data"]
            storage.write_financials("BNY", "2026-Q2", current)
            try:
                storage.write_financials("BK", "2026-Q2", current)
            except RetiredSymbol:
                pass
            else:
                raise AssertionError("Retired maintenance write accepted")
            fallback_results, fallback_failures = run_fallback(["BNY"], db=db, mode="apply")
            require(not fallback_failures, "Bounded scheduled fallback rerun failed")
            require(not any("period" in row for row in fallback_results), "Repaired period was not skipped")
        require(storage.public_symbol("BK") == storage.public_symbol("BNY") == "BNY", "Old/new website routing mismatch")
        try:
            storage.get_ticker_meta("BK")
        except RetiredSymbol:
            pass
        else:
            raise AssertionError("Strict old ticker read accepted")
        require(not db.document("tickers/BNY/financials/2026-Q2").get().exists, "Legacy split record recreated")
        result["verification"] = {"exact_two_payloads": True, "same_server_commit": True,
            "original_audited": True, "other_financial_records_and_descendants_unchanged": len(old_unchanged),
            "metadata_prices_estimates_unchanged": True, "native_reruns_write_counts": commits,
            "scheduled_fallback_rerun": {"results": fallback_results, "failures": fallback_failures,
                                         "scope": "BNY-only function invocation; all nonempty commits blocked; no Cloud Run execution"},
            "retired_write_and_strict_read_rejected": True, "legacy_split_recreated": False,
            "server_commit_time": audit_record["update_time"], "operation_recorded_at": audit["recorded_at"]}
    else:
        require(digest(tree["records"][plan["path"]]) == digest(plan["tree"][plan["path"]]), "Approved period changed")
        require(plan["write_templates"][-1]["path"] not in tree["records"], "Repair already has an audit")
    save(args.output, result)
    print(json.dumps({"output": str(args.output), "period_count": result["period_count"],
                      "financial_record_count": result["financial_record_count"], "dataset_counts": result["dataset_counts"],
                      "verification": result.get("verification"), "sha256": digest(result)}, default=str))


if __name__ == "__main__":
    main()
