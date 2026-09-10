"""Bounded read-only BK/BNY tree capture; private output outside the repository.

Uses the existing named gcloud maintenance account. No partner API, ADC change,
IAM change, provider quote request, or Firestore write is performed.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


def encode(value):
    if isinstance(value, datetime):
        return {"__firestore_timestamp__": value.isoformat()}
    raise TypeError(f"Unsupported export value: {type(value).__name__}")


def digest(value):
    return hashlib.sha256(json.dumps(value, default=encode, sort_keys=True,
                                    separators=(",", ":"), allow_nan=True).encode()).hexdigest()


def capture(db, symbols=("BK", "BNY"), max_documents=8000, max_depth=8, roots=None):
    records = {}
    collections = []

    def visit(ref, depth):
        if len(records) >= max_documents or depth > max_depth:
            raise ValueError("Capture bound reached; export is incomplete")
        snap = ref.get(timeout=30)
        records[ref.path] = {"exists": snap.exists, "data": snap.to_dict(),
                             "update_time": snap.update_time}
        for col in ref.collections(timeout=30):
            collections.append(ref.path + "/" + col.id)
            # list_documents includes missing ancestor documents with descendants.
            for child in col.list_documents(page_size=100, timeout=30):
                visit(child, depth + 1)

    for root in roots or [f"tickers/{symbol}" for symbol in symbols]:
        visit(db.document(root), 0)
    return {"records": records, "collections": sorted(collections)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.output.resolve().is_relative_to(root):
        parser.error("Private exports must be outside the repository")
    if args.output.exists():
        parser.error("Refusing to overwrite an existing export")
    token = subprocess.run([
        "gcloud.cmd", "auth", "print-access-token", "--account=jmaietta@ceorater.com"
    ], check=True, capture_output=True, text=True).stdout.strip()
    from google.cloud import firestore
    from google.oauth2.credentials import Credentials
    db = firestore.Client(project="yfinance-cli", database="(default)",
                          credentials=Credentials(token))
    started = datetime.now(timezone.utc).isoformat()
    tree = capture(db)
    result = {"project": db.project, "database": "(default)",
              "started_at": started, "completed_at": datetime.now(timezone.utc).isoformat(),
              "tree_sha256": digest(tree), **tree}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, default=encode, sort_keys=True, indent=2, allow_nan=True)
    print(json.dumps({"output": str(args.output), "tree_sha256": result["tree_sha256"],
                      "documents": len(tree["records"]), "collections": tree["collections"]}))


if __name__ == "__main__":
    main()
