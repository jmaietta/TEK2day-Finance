"""Build the BK/BNY dry run from saved evidence. This script cannot write Firestore."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from security_identity import BNY_EVENT, digest, portable
from ticker_migration import build_plan, public_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--private-plan", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--binding-review", required=True,
                        help="Explicit record-to-security review basis, not a CIK-only match")
    args = parser.parse_args()
    if args.private_plan.resolve().is_relative_to(ROOT):
        parser.error("Private plan must be outside Git")
    if args.private_plan.exists() or args.manifest.exists():
        parser.error("Refusing to overwrite prior evidence")
    capture = json.loads(args.capture.read_text(encoding="utf-8"))
    bindings = {root: {"security_id": BNY_EVENT["security_id"], "record_sha256": digest(capture["records"][root]),
                       "review_basis": args.binding_review} for root in ("tickers/BK", "tickers/BNY")}
    plan = build_plan(capture, BNY_EVENT, bindings=bindings)
    for path, value in [(args.private_plan, plan), (args.manifest, public_manifest(plan))]:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            json.dump(portable(value), handle, sort_keys=True, indent=2, allow_nan=False)
    print(json.dumps({"plan_sha256": digest(plan), "generation": plan["generation"],
                      "writes": len(plan["writes"]), "conflicts": len(plan["conflicts"]),
                      "manifest": str(args.manifest)}))


if __name__ == "__main__":
    main()
