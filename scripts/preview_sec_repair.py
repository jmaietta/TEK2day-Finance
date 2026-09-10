"""Reproduce the BNY SEC dry run from saved public facts and migration receipt.

No credentials, network or database access. The saved absence is dated evidence,
not a fresh precondition: re-read the live target before approving execution.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sec_fallback import filings_from, build_candidate
from sec_mapping import BINDINGS
from sec_maintenance import make_plan
from security_identity import digest, require


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--migration-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "Refusing to overwrite previous dry run")
    evidence = json.loads(args.evidence.read_text())
    receipt = json.loads(args.migration_receipt.read_text())
    reader = receipt["readers"]
    require(reader["selected_data_matches_manifest"] and "2026-Q2" not in reader["stored_financial_periods"],
            "Saved receipt does not establish June-period absence")
    now = datetime.fromisoformat(receipt["recorded_at"])
    binding = BINDINGS["BNY"]
    filings, _ = filings_from(evidence["submissions"], binding, now)
    filing = next(f for f in filings if f["reportDate"] == "2026-06-30")
    candidate = build_candidate(evidence["facts"], evidence["source_capture"], binding, filing, filings)
    path = reader["route"]["version_path"] + "/financials/2026-Q2"
    plan = make_plan(path, None, candidate)
    output = {"status": "OFFLINE DRY RUN; NOT EXECUTED", "project": "yfinance-cli", "database": "(default)",
              "basis": "Saved verified migration tree; needs a new bounded read before live approval",
              "basis_checked_at": reader["checked_at"], "migration_receipt_sha256": digest(receipt),
              "binding_sha256": digest(binding), "candidate_sha256": digest(candidate),
              "candidate": candidate, "action": plan["action"], "conflicts": plan["conflicts"],
              "filled_fields": plan["filled_fields"], "existing_period": None,
              "proposed_writes": [{"path": path, "operation": "conditional create", "data": "candidate plus execution timestamps and subset warning"},
                                  {"path": plan["audit_path"], "operation": "conditional create",
                                   "data": "exact original/absence, full candidate, selected fields and route state"}],
              "rollback": {"entrypoint": "sec_maintenance.rollback_candidate",
                           "period": "2026-Q2", "candidate_key": plan["audit_path"].split("sec-")[-1],
                           "requirements": "Pause existing identity route; require unchanged applied document and no newer descendants",
                           "effect": "Restore original absence, retain audit and rollback marker, leave route paused for verification"},
              "live_scope": "BNY only; no execution, deployment or enabling approval implied"}
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(output, handle, sort_keys=True, indent=2, allow_nan=False)
    print(json.dumps({"path": path, "writes": 2, "fields": len(plan["filled_fields"]),
                      "candidate_sha256": digest(candidate), "manifest_sha256": digest(output)}))


if __name__ == "__main__":
    main()
