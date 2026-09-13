"""Read-only BNY financial review; bounded native data and fresh free sources.

Private evidence must be outside Git. No financial, audit or cloud writes.
"""
import argparse
from datetime import datetime, timezone
import json
from itertools import islice
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.inspect_ticker_identity import capture, encode
from sec_fallback import SecClient, filings_from, build_candidate, mapped_missing
from sec_maintenance import checked_binding, commit_candidate, candidate_key
from security_identity import BNY_EVENT, digest, require, route_path


def save(path, data):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, default=encode, sort_keys=True, indent=2, allow_nan=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    require(not out.is_relative_to(Path(__file__).resolve().parents[1]), "Private evidence outside Git only")
    out.mkdir(parents=True, exist_ok=False)
    token = subprocess.run(["gcloud.cmd", "auth", "print-access-token", "--account=jmaietta@ceorater.com"],
                           check=True, capture_output=True, text=True).stdout.strip()
    from google.cloud import firestore
    from google.oauth2.credentials import Credentials
    db = firestore.Client(project="yfinance-cli", database="(default)", credentials=Credentials(token))
    binding, root, route = checked_binding(db, "BNY")
    metadata = root.get()
    refs = list(islice(root.collection("financials").list_documents(page_size=40), 41))
    require(len(refs) <= 40, "Financial period directory exceeds bound")
    paths = sorted({r.path for r in refs} | {root.path + "/financials/2026-Q2"})
    tree = capture(db, roots=paths, max_documents=250, max_depth=8)
    native = {"project": db.project, "database": "(default)", "root_path": root.path,
              "route_path": route_path(BNY_EVENT), "route": route,
              "metadata": {"data": metadata.to_dict(), "update_time": metadata.update_time},
              "captured_at": datetime.now(timezone.utc).isoformat(), **tree}
    require(digest(db.document(native["route_path"]).get().to_dict()) == digest(route), "Route changed during capture")
    save(out / "native.json", native)

    # Preserve exact response bytes whose digests the production client records.
    import requests
    class RecordingSession(requests.Session):
        def get(self, url, **kwargs):
            response = super().get(url, **kwargs)
            original = response.iter_content
            def chunks(*a, **kw):
                pieces = []
                for piece in original(*a, **kw):
                    pieces.append(piece)
                    yield piece
                if response.status_code == 200:
                    name = "companyfacts.json" if "/companyfacts/" in url else "submissions.json"
                    with (out / name).open("xb") as handle:
                        handle.write(b"".join(pieces))
            response.iter_content = chunks
            return response
    client = SecClient(session=RecordingSession())
    submissions, submissions_receipt = client.get("submissions/CIK" + binding["cik"] + ".json")
    facts, facts_receipt = client.get("api/xbrl/companyfacts/CIK" + binding["cik"] + ".json")
    now = datetime.now(timezone.utc)
    filings, notices = filings_from(submissions, binding, now)
    save(out / "source-receipts.json", {"submissions": submissions_receipt, "companyfacts": facts_receipt})
    import fetchers
    yahoo = {"quarterly": fetchers.fetch_financials("BNY"), "annual": fetchers.fetch_annual_financials("BNY"),
             "retrieved_at": datetime.now(timezone.utc).isoformat(), "format": "existing normalized Yahoo fetcher output"}
    save(out / "yahoo.json", yahoo)
    plans, outcomes = [], []
    api = db._firestore_api
    real_commit = api.commit
    def no_writes(request=None, **kwargs):
        require(isinstance(request, dict) and not request.get("writes"), "Nonempty database commit blocked")
        return real_commit(request=request, **kwargs)
    with patch.object(api, "commit", side_effect=no_writes):
        for filing in filings:
            try:
                candidate = build_candidate(facts, facts_receipt, binding, filing, filings)
                plan = commit_candidate(db, "BNY", candidate, apply=False, now=now)
                require(digest(plan["before"]) == digest(tree["records"].get(plan["path"], {}).get("data")),
                        "Stored period changed since capture")
                plans.append(plan)
                outcomes.append({"period": candidate["period"], "accession": filing["accessionNumber"],
                                 "action": plan["action"], "fields": plan["filled_fields"],
                                 "conflict_fields": [c["field"] for c in plan["conflicts"]],
                                 "mapped_missing": mapped_missing(plan["before"] or {}, binding, filing["reportDate"]),
                                 "candidate_key": candidate_key(candidate)})
            except Exception as exc:
                outcomes.append({"filing": filing, "error": type(exc).__name__ + ": " + str(exc)})
    save(out / "plans.json", plans)
    summary = {"checked_at": now.isoformat(), "project": db.project, "database": "(default)",
               "native_sha256": digest(native), "document_count_including_missing": len(tree["records"]),
               "collections": tree["collections"], "eligible_filings": filings, "notices": notices,
               "yahoo_periods": {k: [{"period": p["period"], "period_end": p.get("period_end")} for p in yahoo[k]]
                                 for k in ("quarterly", "annual")}, "outcomes": outcomes,
               "database_writes": 0}
    save(out / "review.json", summary)
    print(json.dumps({"output": str(out), "native_sha256": digest(native),
                      "documents": len(tree["records"]), "outcomes": outcomes,
                      "yahoo_periods": summary["yahoo_periods"], "database_writes": 0}, default=str))


if __name__ == "__main__":
    main()
