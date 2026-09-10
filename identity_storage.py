"""Firestore routing for reviewed securities; unrelated issuers keep legacy paths."""
from copy import deepcopy

from security_identity import (IdentityError, digest, event_for, require,
                               resolve_state, route_path)


def context(db, symbol, *, public=False, write=False, transaction=None):
    event = event_for(symbol)
    if event is None:
        return symbol, db.collection("tickers").document(symbol), None, None
    state = db.document(route_path(event)).get(transaction=transaction).to_dict() or {}
    canonical, path = resolve_state(symbol, event, state, public=public, write=write)
    ref = db.document(path)
    snap = ref.get(transaction=transaction)
    target = snap.to_dict() or {}
    if state.get("status") in {"active", "paused"}:
        require(snap.exists and target.get("security_id") == event["security_id"]
                and target.get("issuer_id") == event["issuer_id"] and target.get("symbol") == canonical,
                "missing or mismatched canonical target")
    else:
        require(not target.get("identity_retired") and not target.get("security_id"), "canonical route missing for published identity")
    return canonical, ref, event, state


def guarded_write(db, symbol, entries, *, merge=False, write_once=False, expected=None):
    """Return False for legacy issuers. All reviewed writes fence the route.

    entries is [(relative document path or '', data)]. Existing observations
    survive every update at deterministic revision paths. Caller data is copied.
    """
    if event_for(symbol) is None:
        return False
    from google.cloud import firestore
    from ticker_migration import reconcile

    @firestore.transactional
    def commit(tx):
        canonical, root, event, state = context(db, symbol, write=True, transaction=tx)
        held = [(suffix, data, db.document(root.path + suffix).get(transaction=tx))
                for suffix, data in entries]
        for suffix, incoming, snap in held:
            require(suffix == "" or (suffix.startswith('/') and len(suffix.split('/')) == 3
                    and suffix.split('/')[1] in {'prices', 'financials', 'estimates'}
                    and bool(suffix.split('/')[2])), "invalid maintenance path")
            old = snap.to_dict()
            if expected is not None:
                require(digest(old) == digest(expected), "concurrent financial edit; retry review")
            data = deepcopy(incoming)
            require(data.get("symbol", canonical) == canonical, "write symbol mismatch")
            for key, wanted in [("security_id", event["security_id"]), ("issuer_id", event["issuer_id"]),
                                ("currency", event["to"]["currency"]), ("share_class", event["to"]["share_class"]),
                                ("cusip", event["to"]["cusip"])]:
                if data.get(key) is not None:
                    require(data[key] == wanted, "maintenance security identity changed")
            if data.get("cik") is not None:
                require(str(data["cik"]).zfill(10) == event["to"]["cik"], "maintenance registrant changed")
            if data.get("exchange") is not None:
                require(event["to"]["exchange_mic"] == "XNYS" and data["exchange"] in {"NYSE", "NYQ", "XNYS"},
                        "maintenance listing venue changed")
            if suffix:
                section, key = suffix.split('/')[1:]
                identity_key = "period" if section == "financials" else "date"
                require(data.get(identity_key) == key, "maintenance document period mismatch")
                if old and section == "financials":
                    require(all(old.get(k) == data.get(k) for k in ("period", "period_end", "freq")),
                            "maintenance financial period identity changed")
            ref = db.document(root.path + suffix)
            if write_once and snap.exists:
                # Record revised observations; never discard restatements merely
                # because the financial period already exists.
                prior_values = {k: v for k, v in old.items() if k != "fetched_at"}
                next_values = {k: v for k, v in data.items() if k != "fetched_at"}
                if digest(prior_values) != digest(next_values):
                    tx.set(ref.collection("identity_observations").document(digest(data)), data)
                continue
            if snap.exists:
                tx.set(ref.collection("identity_observations").document(digest(old)), old)
            if merge and old:
                # Metadata refresh may change populated market fields; missing
                # provider fields cannot erase known descriptive metadata.
                data = reconcile(old, data)
            if state.get("status") == "active" and not suffix:
                data.update(symbol=canonical, issuer_id=event["issuer_id"], security_id=event["security_id"],
                            cik=event["to"]["cik"])
                tx.set(db.collection("tickers").document(canonical), data)
            tx.set(ref, data, merge=merge)
    commit(db.transaction())
    return True


def reject_tombstone(meta):
    if meta and (meta.get("renamed_to") or meta.get("security_resolution") is not None):
        raise IdentityError("unreviewed alias metadata")
    return meta
