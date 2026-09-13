"""Prepare, check or execute one immutable SEC gap repair. No scheduler trigger.

Apply requires separate approval of the plan digest and an explicit write gate.
Check uses native transactions with nonempty commits blocked at the RPC layer.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.inspect_ticker_identity import capture
from scripts.run_ticker_migration import restore
from sec_maintenance import reviewed_repair, commit_candidate, candidate_key, rollback_candidate, repair_route_status
from security_identity import digest, portable, require


def read(path):
    return restore(json.loads(path.read_text(encoding="utf-8")))


def save(path, data):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(portable(data), handle, sort_keys=True, indent=2, allow_nan=False)


def public_manifest(plan):
    return {"status": "PREPARED; NOT EXECUTED", "plan_sha256": digest(plan),
            "project": plan["project"], "database": plan["database"], "symbol": plan["symbol"],
            "period": plan["candidate"]["period"], "captured_at": plan["captured_at"],
            "route_path": plan["route_path"], "route_sha256": digest(plan["route"]),
            "candidate_sha256": digest(plan["candidate"]), "candidate_key": candidate_key(plan["candidate"]),
            "source_capture": plan["candidate"]["sec_provenance"]["source_capture"],
            "filled_fields": plan["filled_fields"], "conflict_count": 0,
            "original_tree": [{"path": p, "exists": d["exists"], "sha256": digest(d["data"]),
                               "update_time": d["update_time"]} for p, d in sorted(plan["tree"].items())],
            "original_collections": plan["collections"],
            "proposed_writes": [{"path": w["path"], "operation": "conditional set in one transaction",
                                 "template_sha256": digest(w["data"])} for w in plan["write_templates"]],
            "timestamp_policy": "Only sec_backfilled_at and audit.recorded_at use actual execution time; "
                                "audit approval_plan_sha256 uses this plan digest; applied_sha256 is recomputed. "
                                "All source/report timestamps and existing fetched_at stay frozen.",
            "rollback": {"entrypoint": "sec_maintenance.rollback_candidate", "symbol": plan["symbol"],
                         "period": plan["candidate"]["period"], "candidate_key": candidate_key(plan["candidate"]),
                         "original_sha256": digest(plan["tree"][plan["path"]]["data"]),
                         "requirements": "Pause reviewed route; require unchanged applied record and no later descendants; "
                                         "restore exact original, retain audit/rollback marker, verify before resuming route"},
            "scope": "One BNY financial period and its audit; all nested observations retained. "
                     "No quotes, estimates, metadata, other periods or scheduler changes."}


def check_tree(db, plan):
    """Re-enumerate all descendants before the transaction; never delete them."""
    tree = capture(db, roots=[plan["path"]], max_documents=200, max_depth=8)
    audit_path = plan["write_templates"][-1]["path"]
    saved = tree["records"].get(audit_path, {}).get("data") or {}
    if saved.get("approval_plan_sha256") == digest(plan):
        return  # Transaction verifies idempotent replay/rollback state.
    require(digest(tree["records"]) == digest(plan["tree"]) and tree["collections"] == plan["collections"],
            "Approved tree changed; prepare a new review")


def check_sources(plan):
    """A newer amendment, changed facts or newly available Yahoo blocks repair."""
    from sec_fallback import SecClient, filings_from, build_candidate, finite
    from sec_mapping import BINDINGS
    import fetchers
    binding = BINDINGS[plan["symbol"]]
    client = SecClient()
    submissions, _ = client.get("submissions/CIK" + binding["cik"] + ".json")
    filings, notices = filings_from(submissions, binding, datetime.now(timezone.utc))
    candidate = plan["candidate"]
    form = "10-K" if candidate["freq"] == "FY" else "10-Q"
    matches = [f for f in filings if f["reportDate"] == candidate["period_end"] and f["form"] == form]
    require(len(matches) == 1, "Reviewed report no longer uniquely eligible; amendment or source review required")
    facts, receipt = client.get("api/xbrl/companyfacts/CIK" + binding["cik"] + ".json")
    fresh = build_candidate(facts, receipt, binding, matches[0], filings)
    require(candidate_key(fresh) == candidate_key(candidate), "SEC candidate changed since approval")
    fetch = fetchers.fetch_annual_financials if form == "10-K" else fetchers.fetch_financials
    yahoo = [p for p in fetch(plan["symbol"]) if p.get("period") == candidate["period"]
             and p.get("period_end") == candidate["period_end"]]
    require(len(yahoo) <= 1, "Ambiguous current Yahoo period")
    for field in plan["filled_fields"]:
        section, name = field.split(".", 1)
        require(not yahoo or not finite((yahoo[0].get(section) or {}).get(name)),
                "Yahoo now supplies an approved gap; refresh the repair plan")
    return {"companyfacts": receipt, "candidate_key": candidate_key(fresh), "notices": notices}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare")
    for flag in ("native", "candidates", "private-plan", "manifest"):
        prep.add_argument("--" + flag, type=Path, required=True)
    prep.add_argument("--period", required=True)
    for action in ("check", "apply", "pause", "rollback", "resume"):
        cmd = sub.add_parser(action)
        cmd.add_argument("--plan", type=Path, required=True)
        cmd.add_argument("--approved-plan-sha256", required=True)
        cmd.add_argument("--receipt", type=Path, required=True)
        cmd.add_argument("--confirm-project", required=True, choices=["yfinance-cli"])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    private = args.private_plan if args.action == "prepare" else args.receipt
    require(not private.resolve().is_relative_to(root), "Private evidence must stay outside Git")
    require(not private.exists(), "Use a new output path")
    if args.action == "prepare":
        require(not args.manifest.exists(), "Use a new manifest path")
        candidates = [p["candidate"] for p in read(args.candidates) if p["candidate"]["period"] == args.period]
        require(len(candidates) == 1, "Ambiguous repair candidate")
        plan = reviewed_repair(read(args.native), candidates[0])
        save(args.private_plan, plan)
        save(args.manifest, public_manifest(plan))
        print(json.dumps({"plan_sha256": digest(plan), "writes": len(plan["write_templates"]),
                          "filled_fields": len(plan["filled_fields"])}))
        return
    plan = read(args.plan)
    require(digest(plan) == args.approved_plan_sha256, "Approved plan digest mismatch")
    require(plan["project"] == args.confirm_project and plan["database"] == "(default)", "Wrong database")
    if args.action != "check":
        require(os.getenv("TEK2DAY_ALLOW_SEC_REPAIR") == "1", "Explicit financial write gate required")
    token = subprocess.run(["gcloud.cmd", "auth", "print-access-token", "--account=jmaietta@ceorater.com"],
                           check=True, capture_output=True, text=True).stdout.strip()
    from google.cloud import firestore
    from google.oauth2.credentials import Credentials
    db = firestore.Client(project=plan["project"], database=plan["database"], credentials=Credentials(token))
    if args.action in {"pause", "rollback", "resume"}:
        if args.action == "rollback":
            audit = db.document(plan["write_templates"][-1]["path"]).get().to_dict() or {}
            route = db.document(plan["route_path"]).get().to_dict()
            require(audit.get("approval_plan_sha256") == digest(plan) and
                    digest(route) == digest({**plan["route"], "status": "paused"}),
                    "Approved rollback audit or paused route changed")
            outcome = rollback_candidate(db, plan["symbol"], plan["candidate"]["period"], candidate_key(plan["candidate"]))
        else:
            outcome = repair_route_status(db, plan, "paused" if args.action == "pause" else "active")
        receipt = {"action": args.action, "outcome": outcome, "plan_sha256": digest(plan),
                   "completed_at": datetime.now(timezone.utc).isoformat(),
                   "route": db.document(plan["route_path"]).get().to_dict(),
                   "target_sha256": digest(db.document(plan["path"]).get().to_dict())}
        save(args.receipt, receipt)
        print(json.dumps(portable(receipt)))
        return
    api = db._firestore_api
    real_commit = api.commit
    def guarded_commit(request=None, **kwargs):
        if args.action == "check":
            require(isinstance(request, dict) and not request.get("writes"), "Nonempty database commit blocked")
        return real_commit(request=request, **kwargs)
    with patch.object(api, "commit", side_effect=guarded_commit):
        # Already applied/rolled-back plans use the immutable audit, allowing
        # recovery even when providers are down or their newer facts differ.
        existing = db.document(plan["write_templates"][-1]["path"]).get().to_dict() or {}
        sources = (None if existing.get("approval_plan_sha256") == digest(plan) else check_sources(plan))
        check_tree(db, plan)
        result = commit_candidate(db, plan["symbol"], plan["candidate"],
                                  apply=args.action == "apply", approval=plan)
    target = db.document(plan["path"]).get()
    audit = db.document(result["audit_path"]).get()
    receipt = {"action": args.action, "outcome": result["action"], "plan_sha256": digest(plan),
               "completed_at": datetime.now(timezone.utc).isoformat(),
               "source_recheck": sources,
               "target_sha256": digest(target.to_dict()), "target_update_time": target.update_time,
               "audit_sha256": digest(audit.to_dict()), "audit_update_time": audit.update_time}
    save(args.receipt, receipt)
    print(json.dumps(portable(receipt)))


if __name__ == "__main__":
    main()
