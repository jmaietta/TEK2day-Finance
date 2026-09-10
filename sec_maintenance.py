"""Scheduled SEC fallback and atomic financial writes with retained originals.

SEC_FALLBACK_MODE=off (default), observe (no financial writes), or apply.
Enabling apply is a separate rollout action. Existing scheduler/jobs are reused.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import os

import identity_storage
import storage
from sec_mapping import BINDINGS, PROFILES
from sec_fallback import SecClient, filings_from, build_candidate, merge_candidate, coverage_missing, mapped_missing
from security_identity import digest, event_for, require

logger = logging.getLogger("ydp.sec_fallback")


def checked_binding(db, symbol, transaction=None):
    require(symbol in BINDINGS, "SEC security binding has not been reviewed")
    binding = BINDINGS[symbol]
    canonical, root, event, state = identity_storage.context(db, symbol, write=True, transaction=transaction)
    meta = root.get(transaction=transaction).to_dict() or {}
    require(canonical == symbol == binding["symbol"] and meta.get("active") is not False,
            "Inactive or renamed SEC binding")
    require(not meta.get("renamed_to") and not meta.get("identity_retired"), "Retired SEC target")
    require(str(meta.get("cik")).zfill(10) == binding["cik"] and meta.get("currency") == binding["currency"],
            "Stored registrant/currency differs from SEC binding")
    require(all(meta.get(k) == binding[k] for k in ("issuer_id", "security_id")),
            "Canonical issuer/security binding mismatch; CIK alone cannot authorize this write")
    if event:
        require(digest(event) == binding.get("identity_event_sha256") and state.get("status") == "active",
                "Reviewed identity route changed or is not active")
    else:
        require(binding.get("identity_review"), "Independent identity review required")
    require(binding["profile"] in PROFILES and binding.get("evidence"), "SEC mapping review missing")
    return binding, root, state


def candidate_key(candidate):
    # Retrieval timestamps are not changes to the underlying SEC observation.
    stable = deepcopy(candidate)
    stable["sec_provenance"].pop("source_capture", None)
    return digest(stable)


def make_plan(path, before, candidate):
    selected, filled, conflicts = merge_candidate(before, candidate)
    # Mixed incompatible bases must not produce a Frankenstatement. Retain
    # the candidate for review; a human can select a coherent revised version.
    action = "review" if conflicts else "fill" if filled else "noop"
    return {"path": path, "before": before, "before_sha256": digest(before), "candidate": candidate,
            "selected": selected, "filled_fields": filled, "conflicts": conflicts, "action": action,
            "audit_path": path + "/identity_observations/sec-" + candidate_key(candidate)}


def commit_candidate(db, symbol, candidate, *, apply=False, now=None):
    """Two-document transaction: selected period + immutable source/original audit.

    Re-read identity and destination inside the transaction. All descendants
    stay in place. Crashes cannot expose a partially populated statement. The
    same source rerun does not change observations or ingestion timestamps.
    """
    from google.cloud import firestore
    now = now or datetime.now(timezone.utc)

    @firestore.transactional
    def commit(tx):
        binding, root, state = checked_binding(db, symbol, transaction=tx)
        require(candidate["sec_provenance"]["binding_sha256"] == digest(binding), "Candidate binding changed")
        require(candidate.get("symbol") == symbol and candidate.get("cik") == binding["cik"]
                and candidate.get("security_id") == binding["security_id"]
                and candidate.get("issuer_id") == binding["issuer_id"], "Candidate identity mismatch")
        require(not coverage_missing(candidate), "Incomplete SEC candidate")
        period = candidate["period"]
        import re
        require(re.fullmatch(r"\d{4}-(?:Q[1-4]|FY)", period) is not None, "Invalid financial path")
        ref = root.collection("financials").document(period)
        snap = ref.get(transaction=tx)
        plan = make_plan(ref.path, snap.to_dict(), candidate)
        audit_ref = db.document(plan["audit_path"])
        audit_snap = audit_ref.get(transaction=tx)
        if audit_snap.exists:
            saved = audit_snap.to_dict()
            require(saved.get("candidate_key") == candidate_key(candidate), "Audit key mismatch")
            if saved.get("status") == "rolled_back":
                return {**plan, "action": "held_after_rollback"}
            if saved.get("status") == "review":
                return {**plan, "action": "review"}
            if saved.get("status") == "applied":
                # Original fields still present: idempotent rerun, including
                # later Yahoo additions. A changed selected value needs review.
                selected = saved["selected_values"]
                current = snap.to_dict() or {}
                unchanged = all((current.get(s) or {}).get(f) == value
                                for s, fields in selected.items() for f, value in fields.items())
                return {**plan, "action": "noop" if unchanged else "review_changed_after_apply"}
        if not apply or plan["action"] == "noop":
            return plan
        require(not audit_snap.exists, "Conflicting prior SEC audit; review before retry")
        audit = {"status": "review" if plan["action"] == "review" else "applied",
                 "candidate_key": candidate_key(candidate), "candidate": candidate,
                 "original": snap.to_dict(), "original_update_time": snap.update_time,
                 "conflicts": plan["conflicts"], "filled_fields": plan["filled_fields"],
                 "identity_state": state, "recorded_at": now.isoformat(),
                 "selected_values": {s: {f: plan["selected"][s][f] for f in candidate[s]
                                         if s + "." + f in plan["filled_fields"]} for s in candidate if s in
                                      ("income", "balance_sheet", "cash_flow")}}
        require(len(json.dumps(audit, default=str).encode()) < 750_000, "SEC audit exceeds bounded document size")
        if plan["action"] == "fill":
            selected = deepcopy(plan["selected"])
            if not snap.exists:
                selected["fetched_at"] = candidate["sec_provenance"]["source_capture"]["retrieved_at"]
            selected["sec_backfilled_at"] = now.isoformat()
            # Readers must not mistake a supported subset for the complete
            # provider statement just because the headline anchors arrived.
            selected.setdefault("data_warnings", []).append({"code": "sec_mapped_subset",
                "detail": "SEC supplied reviewed fields; unsupported fields remain unavailable"})
            audit["applied_sha256"] = digest(selected)
            tx.set(ref, selected)
        tx.set(audit_ref, audit)
        return plan

    return commit(db.transaction())


def rollback_candidate(db, symbol, period, key, *, now=None):
    """Conditional reverse transaction, deliberately requires a paused route.

    Pause with the existing identity maintenance mechanism first. Refuse any
    newer child observation or changed period; do not delete descendants. A
    retained rollback marker prevents automatic reapplying the same SEC facts.
    Does not resume the route: verify restoration first, then resume explicitly.
    """
    from google.cloud import firestore
    from security_identity import route_path
    import re
    require(re.fullmatch(r"\d{4}-(?:Q[1-4]|FY)", period) is not None and
            re.fullmatch(r"[0-9a-f]{64}", key) is not None, "Invalid rollback path")
    _, root, event, state = identity_storage.context(db, symbol)
    require(event and state.get("status") == "paused", "Pause reviewed maintenance before SEC rollback")
    ref = root.collection("financials").document(period)
    audit_ref = ref.collection("identity_observations").document("sec-" + key)
    # Enumerate ALL levels, including children of missing ancestor documents.
    # Under a paused route sanctioned writers cannot add observations. Existing
    # descendants are never copied, overwritten or deleted by this repair.
    descendants = []
    def walk(parent, depth=0):
        require(depth <= 8 and len(descendants) < 200, "Rollback descendant bound exceeded")
        for collection in parent.collections():
            for child in collection.list_documents():
                snap = child.get()
                descendants.append(snap)
                walk(child, depth + 1)
    walk(ref)
    saved = audit_ref.get().to_dict() or {}
    require(saved.get("status") in {"applied", "rolled_back"}, "No applied SEC observation to roll back")
    if saved["status"] == "rolled_back":
        return "already_rolled_back"
    applied_time = datetime.fromisoformat(saved["recorded_at"])
    for snap in descendants:
        if snap.reference.path != audit_ref.path:
            require(snap.update_time is not None and snap.update_time <= applied_time,
                    "Newer descendant observation; reviewed reverse plan required")

    @firestore.transactional
    def restore(tx):
        route = db.document(route_path(event)).get(transaction=tx).to_dict()
        current = ref.get(transaction=tx)
        audit = audit_ref.get(transaction=tx).to_dict()
        held = [(s, s.reference.get(transaction=tx)) for s in descendants]
        require(digest(route) == digest(state) and digest(audit) == digest(saved), "Rollback state changed")
        require(digest(current.to_dict()) == saved["applied_sha256"], "Financial record changed; reverse plan required")
        require(all(a.update_time == b.update_time and digest(a.to_dict()) == digest(b.to_dict()) for a, b in held),
                "Descendant changed during rollback")
        if saved["original"] is None:
            tx.delete(ref)  # retains this audit and every nested observation
        else:
            tx.set(ref, saved["original"])
        tx.set(audit_ref, {**saved, "status": "rolled_back",
                          "rolled_back_at": (now or datetime.now(timezone.utc)).isoformat()})
        return "rolled_back_route_still_paused"
    return restore(db.transaction())


def run_fallback(active_symbols, *, yahoo_seen=None, client=None, db=None, mode=None, now=None):
    """Daily review of enrolled issuers, independent of their Yahoo tranche.

    Financial writes stay off by default. Failures are returned to the parent
    job so it cannot claim success while an eligible repair failed.
    """
    mode = mode or os.getenv("SEC_FALLBACK_MODE", "off").strip().lower()
    require(mode in {"off", "observe", "apply"}, "Invalid SEC_FALLBACK_MODE")
    if mode == "off":
        logger.info("SEC fallback disabled (SEC_FALLBACK_MODE=off)")
        return [], []
    import fetchers
    now = now or datetime.now(timezone.utc)
    db, client = db or storage.get_db(), client or SecClient()
    results, failures = [], []
    logger.info("SEC fallback mode=%s: %d reviewed securities in %d active symbols; other issuers require enrollment review",
                mode, len(set(active_symbols) & set(BINDINGS)), len(active_symbols))
    for symbol in sorted(set(active_symbols) & set(BINDINGS)):
        try:
            binding, root, _ = checked_binding(db, symbol)
            submissions, _ = client.get(f"submissions/CIK{binding['cik']}.json")
            filings, notices = filings_from(submissions, binding, now)
            results.extend({"symbol": symbol, **n} for n in notices)
            if any(n["status"] == "review" for n in notices):
                failures.append(symbol + ":amended_filings_need_review")
            if not filings:
                continue
            # Current Yahoo gets first chance; reuse this job's existing pulls.
            yahoo = (yahoo_seen or {}).get(symbol)
            if yahoo is None:
                ysym = symbol.replace(".", "-")
                yahoo = (fetchers.fetch_financials(ysym), fetchers.fetch_annual_financials(ysym))
            all_yahoo = [dict(d, symbol=symbol) for docs in yahoo for d in (docs or [])]
            facts = receipt = None
            for filing in filings:
                end = filing["reportDate"]
                from datetime import date
                d = date.fromisoformat(end)
                period = f"{d.year}-FY" if filing["form"] == "10-K" else f"{d.year}-Q{(d.month - 1)//3+1}"
                stored = root.collection("financials").document(period).get().to_dict()
                if stored is not None and not mapped_missing(stored, binding, end):
                    continue
                matches = [x for x in all_yahoo if x.get("period") == period and x.get("period_end") == end]
                require(len(matches) <= 1, "Ambiguous Yahoo period")
                if matches:
                    # Only use the requested report's exact period/frequency.
                    require(matches[0].get("freq", "Q") == ("FY" if filing["form"] == "10-K" else "Q"),
                            "Yahoo period frequency mismatch")
                    held = stored or {k: matches[0][k] for k in ("symbol", "period", "period_end")}
                    fresh, _ = storage.merge_financial_doc(held, matches[0])
                    if mode == "apply":
                        storage.write_financials(symbol, period, matches[0])
                        storage.backfill_financials(symbol, period, matches[0], db=db)
                        fresh = root.collection("financials").document(period).get().to_dict()
                    if not mapped_missing(fresh, binding, end):
                        results.append({"symbol": symbol, "period": period, "status": "yahoo_available", "mode": mode})
                        continue
                if facts is None:
                    facts, receipt = client.get(f"api/xbrl/companyfacts/CIK{binding['cik']}.json")
                candidate = build_candidate(facts, receipt, binding, filing, filings)
                plan = commit_candidate(db, symbol, candidate, apply=mode == "apply", now=now)
                result = {"symbol": symbol, "period": period, "status": plan["action"],
                          "fields": len(plan["filled_fields"]), "conflicts": len(plan["conflicts"]),
                          "source": "SEC EDGAR", "path": plan["path"], "candidate_sha256": digest(candidate),
                          "mode": mode}
                results.append(result)
                if plan["action"].startswith("review") or plan["action"] == "held_after_rollback":
                    failures.append(symbol + ":" + period + ":" + plan["action"])
        except Exception as exc:
            logger.exception("SEC fallback failed for %s", symbol)
            failures.append(symbol + ":" + type(exc).__name__ + ":" + str(exc))
    for result in results:
        logger.info("SEC_FALLBACK %s", json.dumps(result, default=str, sort_keys=True))
    return results, failures
