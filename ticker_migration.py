"""Offline reconciliation and a guarded, resumable Firestore rename executor.

No live action runs on import. A plan retains every original observation, every
nested document and every populated conflict. The destination is published by
one transaction only after the entire staged tree has been verified.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math

from security_identity import (digest, portable, require, validate_event, REVIEWED_EVENTS,
                               route_path, version_path)


def missing(value):
    return value is None or (isinstance(value, float) and not math.isfinite(value))


def reconcile(old, new, path="", conflicts=None):
    """Destination wins populated conflicts; fill null/absent only. Zero stays.

    There is no automatic newer-is-better rule, including share counts. A
    restatement is a separately retained observation requiring explicit review.
    """
    conflicts = conflicts if conflicts is not None else []
    if isinstance(old, dict) and isinstance(new, dict):
        out = deepcopy(new)
        for key, value in old.items():
            child = f"{path}/{key}"
            if key not in new:
                out[key] = deepcopy(value)
            else:
                out[key] = reconcile(value, new[key], child, conflicts)
        return out
    if missing(new):
        return deepcopy(old if not missing(old) else new)
    if not missing(old) and digest(old) != digest(new):
        conflicts.append({"field": path, "source": portable(old), "destination": portable(new),
                          "selection": "populated_destination", "review_required": True})
    return deepcopy(new)


def build_plan(capture, event, *, bindings):
    validate_event(event)
    require(capture["project"] == "yfinance-cli" and capture["database"] == "(default)", "wrong database")
    old, new = event["from"]["symbol"], event["to"]["symbol"]
    roots = [f"tickers/{old}", f"tickers/{new}"]
    records = capture["records"]
    require(set(bindings) == set(roots), "explicit independent record bindings required")
    for root in roots:
        require(root in records, "root not inspected")
        bind = bindings[root]
        require(bind.get("security_id") == event["security_id"] and bind.get("review_basis"), "unreviewed record binding")
        require(bind.get("record_sha256") == digest(records[root]), "binding changed")
        data = records[root].get("data") or {}
        require(not data.get("renamed_to") and not data.get("security_resolution"), "unresolved tombstone")
        if data.get("cik") is not None:
            require(str(data["cik"]).zfill(10) == event["from"]["cik"], "registrant conflict")
        require(data.get("symbol", root.split('/')[-1]) == root.split('/')[-1], "record symbol conflict")
        if data.get("currency"):
            require(data["currency"] == event["from"]["currency"], "record currency conflict")
        if data.get("exchange"):
            # Provider spellings mapped explicitly for this reviewed NYSE event.
            require(event["from"]["exchange_mic"] == "XNYS" and data["exchange"] in {"NYSE", "NYQ", "XNYS"}, "record venue conflict")
    for path in records:
        require(any(path == root or path.startswith(root + "/") for root in roots), "out-of-scope document")
        require(len(path.split("/")) % 2 == 0, "invalid document path")
        data = records[path].get("data") or {}
        for key, expected in [("security_id", event["security_id"]), ("issuer_id", event["issuer_id"]),
                              ("share_class", event["from"]["share_class"]), ("cusip", event["from"]["cusip"])]:
            if data.get(key) is not None:
                require(data[key] == expected, "document security identity conflict")
    require(records[roots[0]]["exists"] or records[roots[1]]["exists"], "both records missing")
    grouped = {}
    for path, snap in records.items():
        root = next(root for root in roots if path == root or path.startswith(root + "/"))
        relative = path[len(root):]
        grouped.setdefault(relative, {})[root] = snap
    selected, conflicts = {}, []
    for relative, pair in sorted(grouped.items()):
        a = (pair.get(roots[0]) or {}).get("data")
        b = (pair.get(roots[1]) or {}).get("data")
        if a is None and b is None:
            continue  # absent parents still have independently enumerated children
        if a is not None and b is not None and relative.startswith("/financials/"):
            for key in ("period", "period_end", "freq"):
                require(a.get(key) == b.get(key), "financial period identity conflict")
        merged = reconcile(a, b, relative, conflicts) if b is not None else deepcopy(a)
        if relative == "" or (relative.count("/") == 2 and relative.split('/')[1] in {"prices", "estimates", "financials"}):
            for obj in (a, b):
                if obj and obj.get("symbol"):
                    require(obj["symbol"] in {old, new}, "nested security conflict")
            merged["symbol"] = new
        if relative == "":
            merged.update(issuer_id=event["issuer_id"], security_id=event["security_id"],
                          cik=event["to"]["cik"], active=True)
        selected[relative] = merged
    # Symbol transitions are expected, but retained in original observations.
    conflicts = [c for c in conflicts if not c["field"].endswith("/symbol")]
    seed = {"event": event, "records": records, "collections": capture["collections"], "bindings": bindings,
            "selection_rule": "destination-populated-then-source-fill-v1"}
    generation = digest(seed)
    target = version_path(event, generation)
    writes = [{"path": target + relative, "data": data, "sha256": digest(data)}
              for relative, data in sorted(selected.items())]
    # Preserve all original snapshots and native timestamps in separate audit
    # records. No archive collection is named prices/estimates/financials.
    for path, snap in sorted(records.items()):
        value = {"source_path": path, "snapshot": snap}
        writes.append({"path": f"identity_migrations/{generation}/observations/{digest(path)}",
                       "data": value, "sha256": digest(value)})
    state = {"event_sha256": digest(event), "generation": generation,
             "version_path": target, "status": "active"}
    return {
        "format": "ticker-migration-v1", "project": capture["project"], "database": capture["database"],
        "generation": generation, "event": deepcopy(event), "bindings": deepcopy(bindings),
        "source_records": deepcopy(records), "source_collections": capture["collections"],
        "source_digest": digest({"records": records, "collections": capture["collections"]}),
        "conflicts": conflicts, "writes": writes,
        "publish": {"route_path": route_path(event), "route": state,
                    "old_path": roots[0], "new_path": roots[1], "new_metadata": selected[""]},
        "rollback": {"restore_roots": {root: records[root] for root in roots},
                     "route_status": "rolled_back", "retain_version_and_observations": True,
                     "refuse_if_active_data_changed": True},
        "coverage": {"quotes": "not captured", "earnings": "requires period/completeness review",
                     "estimates": "requires explicit horizons and post-results provenance", "history": "preserved, not refreshed"},
    }


def validate_plan(plan):
    capture = {"project": plan["project"], "database": plan["database"],
               "records": plan["source_records"], "collections": plan["source_collections"]}
    require(digest(plan) == digest(build_plan(capture, plan["event"], bindings=plan["bindings"])), "plan altered")
    return plan


def verify_sources(plan, capture):
    require(digest({"records": capture["records"], "collections": capture["collections"]}) == plan["source_digest"],
            "source tree changed; capture and review a new plan")


def public_manifest(plan):
    """Exact paths/digests/counts for review, without private financial payloads."""
    return {k: v for k, v in {
        "format": plan["format"], "project": plan["project"], "database": plan["database"],
        "generation": plan["generation"], "plan_sha256": digest(plan), "event_sha256": digest(plan["event"]),
        "source_digest": plan["source_digest"], "source_collections": plan["source_collections"],
        "source_documents": [{"path": p, "exists": s["exists"], "sha256": digest(s), "update_time": s["update_time"]}
                             for p, s in sorted(plan["source_records"].items())],
        "proposed_writes": [{"path": w["path"], "sha256": w["sha256"]} for w in plan["writes"]],
        "conflict_count": len(plan["conflicts"]),
        "conflict_dispositions": [{"field": c["field"], "source_sha256": digest(c["source"]),
                                   "destination_sha256": digest(c["destination"]),
                                   "selection": c["selection"], "review_required": True}
                                  for c in plan["conflicts"]],
        "publish": {k: v for k, v in plan["publish"].items() if k != "new_metadata"},
        "rollback": {"root_paths": list(plan["rollback"]["restore_roots"]),
                     "retain_version_and_observations": True, "refuse_if_active_data_changed": True},
        "coverage": plan["coverage"],
    }.items()}


class FirestoreMigration:
    """Explicitly invoked after separate approval. No delete of source trees.

    Deploy guarded writers and drain old executions before stage(). Every
    routed write transaction reads the same route document as activation.
    """
    def __init__(self, db, plan, capture_tree):
        validate_plan(plan)
        require(plan["event"] in REVIEWED_EVENTS, "event is not in the reviewed registry")
        require(db.project == plan["project"] and db._database == plan["database"], "wrong database")
        self.db, self.plan, self.capture_tree = db, plan, capture_tree

    def _atomic(self, fn):
        from google.cloud import firestore
        return firestore.transactional(fn)(self.db.transaction())

    def _state(self, transaction):
        ref = self.db.document(self.plan["publish"]["route_path"])
        return ref, ref.get(transaction=transaction).to_dict() or {}

    def _owned(self, state):
        require(state.get("generation") == self.plan["generation"], "another migration owns route")

    def stage(self):
        plan, db = self.plan, self.db
        held = db.document(plan["publish"]["route_path"]).get().to_dict() or {}
        if held.get("status") == "active":
            self._owned(held)
            return  # Published reruns must never copy stale source data again.
        def lock(tx):
            ref, state = self._state(tx)
            if state:
                self._owned(state)
                require(state["status"] == "staging", "already published or rolled back; do not restage")
            tx.set(ref, {**plan["publish"]["route"], "status": "staging"})
        self._atomic(lock)
        verify_sources(plan, self.capture_tree())
        for write in plan["writes"]:
            ref = db.document(write["path"])
            held = ref.get()
            if held.exists:
                require(digest(held.to_dict()) == write["sha256"], "staging conflict; do not overwrite")
            else:
                ref.create(write["data"])
        self.verify_staged()
        verify_sources(plan, self.capture_tree())

    def verify_staged(self):
        # Caller supplies recursive capture, including any unexpected descendants.
        expected = {w["path"]: w["sha256"] for w in self.plan["writes"]}
        for path, sha in expected.items():
            snap = self.db.document(path).get()
            require(snap.exists and digest(snap.to_dict()) == sha, "staged write missing or changed")
        from scripts.inspect_ticker_identity import capture
        # Full recursive verification cannot be replaced with document counts.
        for root in (self.plan["publish"]["route"]["version_path"],
                     f"identity_migrations/{self.plan['generation']}"):
            tree = capture(self.db, roots=[root])
            actual = {p: digest(s["data"]) for p, s in tree["records"].items() if s["exists"]}
            subset = {p: sha for p, sha in expected.items() if p == root or p.startswith(root + '/')}
            require(actual == subset, "unexpected or missing nested staged document")

    def publish(self):
        plan, db = self.plan, self.db
        held = db.document(plan["publish"]["route_path"]).get().to_dict() or {}
        if held.get("status") == "active":
            self._owned(held)
            return
        self.verify_staged()
        verify_sources(plan, self.capture_tree())
        def activate(tx):
            ref, state = self._state(tx)
            self._owned(state)
            require(state["status"] == "staging", "not staged")
            roots = {p: db.document(p).get(transaction=tx) for p in plan["rollback"]["restore_roots"]}
            for p, snap in roots.items():
                expected = plan["source_records"][p]
                require(snap.exists == expected["exists"] and digest(snap.to_dict()) == digest(expected["data"]), "root changed")
            tx.set(db.document(plan["publish"]["new_path"]), plan["publish"]["new_metadata"])
            old = deepcopy(plan["source_records"][plan["publish"]["old_path"]]["data"] or {})
            old.update(active=False, identity_retired=plan["event"]["event_id"])
            tx.set(db.document(plan["publish"]["old_path"]), old)
            tx.set(ref, {**plan["publish"]["route"], "published_at": datetime.now(timezone.utc).isoformat()})
        self._atomic(activate)

    def rollback(self):
        plan, db = self.plan, self.db
        held = db.document(plan["publish"]["route_path"]).get().to_dict() or {}
        if held.get("status") == "rolled_back":
            self._owned(held)
            return
        def pause(tx):
            ref, state = self._state(tx)
            self._owned(state)
            require(state["status"] in {"active", "paused", "staging"}, "not rollbackable")
            tx.set(ref, {**state, "status": "paused" if state["status"] != "staging" else "staging"})
        self._atomic(pause)
        # If maintenance has added/revised data, leave it paused, retain every
        # observation and require a separately reviewed reverse-migration plan.
        state = db.document(plan["publish"]["route_path"]).get().to_dict()
        if state["status"] == "paused":
            self.verify_staged()
        def restore(tx):
            ref, state = self._state(tx)
            self._owned(state)
            require(state["status"] in {"paused", "staging"}, "rollback race")
            if state["status"] == "paused":
                expected_old = deepcopy(plan["source_records"][plan["publish"]["old_path"]]["data"] or {})
                expected_old.update(active=False, identity_retired=plan["event"]["event_id"])
                for path, expected in [(plan["publish"]["old_path"], expected_old),
                                       (plan["publish"]["new_path"], plan["publish"]["new_metadata"])]:
                    require(digest(db.document(path).get(transaction=tx).to_dict()) == digest(expected), "published root changed")
                for path, snap in plan["rollback"]["restore_roots"].items():
                    if snap["exists"]:
                        tx.set(db.document(path), snap["data"])
                    else:
                        tx.delete(db.document(path))  # only the root created by this plan
            tx.set(ref, {**state, "status": "rolled_back"})
        self._atomic(restore)
