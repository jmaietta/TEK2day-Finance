"""Explicit maintenance action on an approved immutable plan. Never a scheduler.

Stage/publish/rollback require the plan digest, project, and a separate operator
confirmation that guarded writers are deployed and old executions are drained.
The user must authorize the concrete live action separately from preparation.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from security_identity import digest, require
from ticker_migration import FirestoreMigration, validate_plan
from scripts.inspect_ticker_identity import capture


def restore(value):
    if isinstance(value, dict):
        if set(value) == {"__firestore_timestamp__"}:
            return datetime.fromisoformat(value["__firestore_timestamp__"])
        if set(value) == {"__nonfinite__"}:
            return float(value["__nonfinite__"])
        return {k: restore(v) for k, v in value.items()}
    if isinstance(value, list):
        return [restore(v) for v in value]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["stage", "publish", "rollback"])
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--approved-plan-sha256", required=True)
    parser.add_argument("--confirm-project", required=True)
    parser.add_argument("--writers-deployed-and-drained", action="store_true")
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    plan = restore(json.loads(args.plan.read_text(encoding="utf-8")))
    validate_plan(plan)
    require(digest(plan) == args.approved_plan_sha256, "approved plan digest mismatch")
    require(args.confirm_project == "yfinance-cli", "wrong project")
    require(args.writers_deployed_and_drained, "guarded deployment/drain required")
    require(os.getenv("TEK2DAY_ALLOW_IDENTITY_WRITES") == "1", "live writes not enabled")
    require(not args.receipt.exists(), "receipt already exists")
    token = subprocess.run(["gcloud.cmd", "auth", "print-access-token", "--account=jmaietta@ceorater.com"],
                           check=True, capture_output=True, text=True).stdout.strip()
    from google.cloud import firestore
    from google.oauth2.credentials import Credentials
    db = firestore.Client(project="yfinance-cli", database="(default)", credentials=Credentials(token))
    migration = FirestoreMigration(db, plan, lambda: capture(db))
    getattr(migration, args.action)()
    from datetime import timezone
    receipt = {"action": args.action, "plan_sha256": digest(plan),
               "completed_at": datetime.now(timezone.utc).isoformat(),
               "route": db.document(plan["publish"]["route_path"]).get().to_dict()}
    with args.receipt.open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle, sort_keys=True, indent=2)
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
