"""Capture one BNY quarter read-only, or prepare its SEC repair entirely offline.

Neither command can write Firestore. Private captures/plans stay outside Git.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.sec_financial_fallback import candidate, fill_gaps, PROFILE
from scripts.inspect_ticker_identity import capture, encode
from security_identity import BNY_EVENT, digest, require, route_path, version_path

ROOT = Path(__file__).resolve().parents[1]


def capture_quarter(db):
    import identity_storage
    before = db.document(route_path(BNY_EVENT)).get().to_dict()
    _, root, event, state = identity_storage.context(db, "BNY")
    require(state.get("status") == "active", "Migration must be active")
    tree = capture(db, roots=[root.path + "/financials/2026-Q2"], max_documents=200, max_depth=8)
    after = db.document(route_path(event)).get().to_dict()
    require(digest(before) == digest(after) == digest(state), "Route changed during capture")
    return {"project": db.project, "database": "(default)", "route": state,
            "captured_at": datetime.now(timezone.utc).isoformat(), **tree}


def plan_repair(captured, incoming):
    require(captured["project"] == "yfinance-cli" and captured["database"] == "(default)", "Wrong database")
    state = captured["route"]
    require(state.get("status") == "active" and state.get("event_sha256") == digest(BNY_EVENT), "Wrong active route")
    root = version_path(BNY_EVENT, state["generation"])
    require(root == state["version_path"], "Wrong canonical generation")
    target = root + "/financials/2026-Q2"
    records = captured["records"]
    require(target in records and all(p == target or p.startswith(target + "/") for p in records), "Capture scope mismatch")
    original = records[target]
    require(original["exists"] == (original["data"] is not None), "Inconsistent capture")
    merged, filled, conflicts = fill_gaps(original["data"], incoming)
    plan = {"schema": 1, "profile": PROFILE, "project": captured["project"], "database": captured["database"],
            "target": target, "route_path": route_path(BNY_EVENT), "expected_route": state,
            "original_tree": records, "original_collections": captured["collections"],
            "capture_sha256": digest(captured), "candidate": incoming, "selected": merged,
            "filled_fields": filled, "conflicts": conflicts,
            "selection": "Fill missing/null/nonfinite only; retain populated values, including zero and share counts",
            "ready_for_execution": False,
            "pending": ["Review exact candidate and conflicts", "Guarded executor and recovery verification",
                        "Separate approval for this financial repair; rename migration approval is already fulfilled"]}
    key = digest(plan)
    audit = target + "/identity_observations/sec-" + key
    plan["proposed_writes"] = ([{"path": target, "operation": "set" if original["exists"] else "create",
                                "data_sha256": digest(merged)},
                               {"path": audit, "operation": "create",
                                "contains": "original tree, full SEC candidate, selected fields, conflicts and plan digest"}]
                              if filled else [])
    plan["rollback"] = {"target": target, "operation": "restore" if original["exists"] else "delete_created_document",
        "original_sha256": digest(original["data"]), "retain_audit_path": audit,
        "requirements": ["Fence canonical maintenance using existing route pause before rollback",
                         "Recapture every descendant while fenced; refuse unexpected observations or changed selected document",
                         "Restore only exact captured original (or original absence), retaining all audits",
                         "Atomically restore route activation with final rollback; no identity-unaware code rollback",
                         "Changed active data requires a separately reviewed reverse plan"]}
    return plan


def public_manifest(plan):
    return {k: plan[k] for k in ("schema", "profile", "project", "database", "target", "route_path",
            "capture_sha256", "filled_fields", "selection", "ready_for_execution", "pending", "proposed_writes", "rollback")} | {
        "plan_sha256": digest(plan), "candidate_sha256": digest(plan["candidate"]),
        "captured_documents_including_missing": len(plan["original_tree"]),
        "captured_descendants": len(plan["original_tree"]) - 1,
        "conflict_count": len(plan["conflicts"]),
        "candidate_counts": {s: len(plan["candidate"][s]) for s in ("income", "balance_sheet", "cash_flow")},
        "source": plan["candidate"]["sec_provenance"]["capture"],
        "execution": "NOT EXECUTED; offline preflight, not an approved or active automatic fallback"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    cap = sub.add_parser("capture")
    cap.add_argument("--output", type=Path, required=True)
    dry = sub.add_parser("plan")
    for flag in ("capture", "facts", "receipt", "private-plan", "manifest"):
        dry.add_argument("--" + flag, type=Path, required=True)
    args = parser.parse_args()
    private = args.output if args.mode == "capture" else args.private_plan
    require(not private.resolve().is_relative_to(ROOT), "Private evidence must be outside Git")
    require(not private.exists(), "Refusing to overwrite evidence")
    if args.mode == "capture":
        token = subprocess.run(["gcloud.cmd", "auth", "print-access-token", "--account=jmaietta@ceorater.com"],
                               check=True, capture_output=True, text=True).stdout.strip()
        from google.cloud import firestore
        from google.oauth2.credentials import Credentials
        db = firestore.Client(project="yfinance-cli", database="(default)", credentials=Credentials(token))
        result = capture_quarter(db)
    else:
        require(not args.manifest.exists(), "Refusing to overwrite manifest")
        raw = args.facts.read_bytes()
        receipt = json.loads(args.receipt.read_text())
        require(hashlib.sha256(raw).hexdigest() == receipt["sha256"], "Source bytes do not match capture receipt")
        result = plan_repair(json.loads(args.capture.read_text()), candidate(json.loads(raw), receipt))
        with args.manifest.open("x", encoding="utf-8") as handle:
            json.dump(public_manifest(result), handle, sort_keys=True, indent=2, allow_nan=False)
    with private.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, default=encode, sort_keys=True, indent=2, allow_nan=True)
    print(json.dumps({"mode": args.mode, "sha256": digest(result), "private_output": str(private)}))


if __name__ == "__main__":
    main()
