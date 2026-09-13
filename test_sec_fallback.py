"""Offline SEC translation, scheduling and atomic recovery regression tests.

The bounded BNY facts fixture is public SEC data, not a private database export.
All storage fixtures and industrial-company facts below are synthetic.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import envelope
import sec_fallback as sf
import sec_maintenance as sm
from sec_mapping import BINDINGS
from security_identity import BNY_EVENT, IdentityError, digest, route_path, version_path

NOW = datetime(2026, 9, 10, 23, tzinfo=timezone.utc)


@pytest.fixture
def evidence():
    result = json.loads((Path(__file__).parent / "tests/fixtures/sec_bny_2026.json").read_text())
    additional = json.loads((Path(__file__).parent / "tests/fixtures/sec_bny_2026_additional.json").read_text())
    result['facts']['facts']['us-gaap'].update(additional['us-gaap'])
    return result


def translated(evidence):
    filings, _ = sf.filings_from(evidence["submissions"], BINDINGS["BNY"], NOW)
    return sf.build_candidate(evidence["facts"], evidence["source_capture"], BINDINGS["BNY"], filings[-1], filings)


def test_real_q2_matches_independently_reviewed_pdf(evidence):
    doc = translated(evidence)
    assert doc["income"]["Total Revenue"] == 5_698_000_000
    assert doc["income"]["Net Income"] == 1_761_000_000
    assert doc["income"]["Net Income Including Noncontrolling Interests"] == 1_792_000_000
    assert doc["income"]["Net Income Common Stockholders"] == 1_696_000_000
    assert doc["income"]["Diluted EPS"] == 2.45
    assert doc["income"]["Diluted Average Shares"] == 692_223_000
    assert doc["balance_sheet"]["Total Assets"] == 525_019_000_000
    assert doc["cash_flow"]["Operating Cash Flow"] == 2_462_000_000
    assert doc["cash_flow"]["Investing Cash Flow"] == 38_211_000_000
    assert doc["cash_flow"]["Financing Cash Flow"] == -38_782_000_000
    assert doc["cash_flow"]["Capital Expenditure"] == -487_000_000
    lineage = doc["sec_provenance"]["fields"]["cash_flow.Operating Cash Flow"]
    assert [v["value"] for v in lineage["terms"]] == [-551_000_000, -3_013_000_000]
    assert doc["period_start"] == "2026-04-01"
    assert "Cash And Cash Equivalents" not in doc["balance_sheet"]
    assert "Long Term Debt" not in doc["balance_sheet"]


def test_exact_seven_day_boundary(evidence):
    boundary = datetime(2026, 8, 7, 20, 31, 16, tzinfo=timezone.utc)
    before, _ = sf.filings_from(evidence["submissions"], BINDINGS["BNY"], boundary - timedelta(microseconds=1))
    after, _ = sf.filings_from(evidence["submissions"], BINDINGS["BNY"], boundary)
    assert [r["reportDate"] for r in before] == ["2026-03-31"]
    assert [r["reportDate"] for r in after] == ["2026-03-31", "2026-06-30"]


def test_date_only_evidence_does_not_invent_timestamp(evidence):
    evidence["submissions"]["filings"]["recent"].pop("acceptanceDateTime")
    reports, _ = sf.filings_from(evidence["submissions"], BINDINGS["BNY"], NOW)
    assert reports[-1]["accepted_at"] is None
    assert reports[-1]["eligible_after"] == "2026-08-08T06:00:00+00:00"
    assert reports[-1]["filing_date_precision"] == "day"


@pytest.mark.parametrize("change", ["cik", "venue", "ticker", "ambiguous", "malformed"])
def test_submission_identity_and_structure_refused(evidence, change):
    s = evidence["submissions"]
    if change == "cik": s["cik"] = 1
    if change == "venue": s["exchanges"][s["tickers"].index("BNY")] = "Nasdaq"
    if change == "ticker": s["tickers"] = ["REUSED"]
    if change == "ambiguous": s["tickers"].append("BNY")
    if change == "malformed": s["filings"]["recent"]["reportDate"].pop()
    with pytest.raises(IdentityError):
        sf.filings_from(s, BINDINGS["BNY"], NOW)


def test_amended_filing_never_silently_selected(evidence):
    recent = evidence["submissions"]["filings"]["recent"]
    for key, values in recent.items(): values.append(values[0])
    recent["form"][-1] = "10-Q/A"
    recent["accessionNumber"][-1] = "0001390777-26-999999"
    reports, notices = sf.filings_from(evidence["submissions"], BINDINGS["BNY"], NOW)
    assert all(r["reportDate"] != "2026-06-30" for r in reports)
    assert notices[0]["status"] == "review"


@pytest.mark.parametrize("change", ["conflict", "unit", "nan", "dimension", "wrong_dates", "different_cik"])
def test_fact_ambiguities_fail_closed(evidence, change):
    c = evidence["facts"]["facts"]["us-gaap"]["NetIncomeLoss"]["units"]
    vals = c["USD"]
    row = next(v for v in vals if v.get("start") == "2026-04-01")
    if change == "conflict": vals.append({**row, "val": 99})
    if change == "unit": c["CAD"] = c.pop("USD")
    if change == "nan": row["val"] = float("nan")
    if change == "dimension": row["dimensions"] = {"class": "preferred"}
    if change == "wrong_dates": row["start"] = "2026-01-01"
    if change == "different_cik": evidence["facts"]["cik"] = 1
    with pytest.raises(IdentityError): translated(evidence)


def test_cash_bridge_requires_explicit_basis_review(evidence):
    binding = deepcopy(BINDINGS["BNY"])
    binding["cash_bridges"] = {}
    reports, _ = sf.filings_from(evidence["submissions"], binding, NOW)
    with pytest.raises(IdentityError, match="Operating Cash Flow"):
        sf.build_candidate(evidence["facts"], evidence["source_capture"], binding, reports[-1], reports)


def test_annual_and_noncalendar_year_use_exact_duration():
    binding = {**deepcopy(BINDINGS["BNY"]), "profile": "us-gaap-industrial-v1", "per_share_periods": [],
               "periods_from": "2025-07-01"}
    filing = {"accessionNumber": "0001390777-26-999998", "filingDate": "2026-08-01", "reportDate": "2026-06-30",
              "form": "10-K", "source_url": "https://example.invalid/synthetic"}
    facts = {"cik": 1390777, "facts": {"us-gaap": {}}}
    for tag, value, instant in [("NetIncomeLoss", 200, False),
                               ("RevenueFromContractWithCustomerExcludingAssessedTax", 1000, False),
                               ("Assets", 3000, True), ("NetCashProvidedByUsedInOperatingActivities", 400, False)]:
        fact = {"accn": filing["accessionNumber"], "filed": filing["filingDate"], "form": "10-K",
                "end": "2026-06-30", "val": value, "fp": "FY"}
        if not instant: fact["start"] = "2025-07-01"
        facts["facts"]["us-gaap"][tag] = {"units": {"USD": [fact]}}
    doc = sf.build_candidate(facts, {}, binding, filing, [filing])
    assert doc["period"] == "2026-FY" and doc["freq"] == "FY"
    assert doc["period_start"] == "2025-07-01" and doc["income"]["Total Revenue"] == 1000
    assert "Diluted EPS" not in doc["income"]


def test_zero_and_populated_share_counts_survive_null_fill(evidence):
    incoming = translated(evidence)
    old = {k: deepcopy(v) for k, v in incoming.items() if k != "sec_provenance"}
    old["income"]["Net Income"] = 0
    old["income"]["Diluted Average Shares"] = 123
    old["balance_sheet"]["Total Assets"] = float("nan")
    old["cash_flow"] = None
    before = digest(old)
    merged, fields, conflicts = sf.merge_candidate(old, incoming)
    assert digest(old) == before
    assert merged["income"]["Net Income"] == 0 and merged["income"]["Diluted Average Shares"] == 123
    assert merged["cash_flow"]["Operating Cash Flow"] == 2_462_000_000
    assert len(conflicts) == 2 and "balance_sheet.Total Assets" in fields
    assert sm.make_plan("synthetic/path", old, incoming)["action"] == "review"


@pytest.mark.parametrize("key,value", [("period", "2026-FY"), ("period_end", "2026-03-31"),
                                       ("security_id", "preferred"), ("cik", "0000000001")])
def test_existing_identity_period_collisions_refused(evidence, key, value):
    candidate = translated(evidence)
    old = deepcopy(candidate)
    old[key] = value
    with pytest.raises(IdentityError): sf.merge_candidate(old, candidate)


@pytest.fixture
def db(monkeypatch):
    # Reuse reference/query mocks; replace commit with an ATOMIC fake capable
    # of interrupted-commit tests (the older fixture commits writes serially).
    from test_ticker_identity import DB, Transaction
    from google.cloud import firestore
    def atomic_commit(self):
        old_data, old_times, old_writes = deepcopy(self.db.data), deepcopy(self.db.times), self.db.writes
        try:
            for ref, data, merge in self.pending:
                if data is None:
                    self.db.data.pop(ref.path, None)
                else: ref.set(data, merge=merge)
        except Exception:
            self.db.data, self.db.times, self.db.writes = old_data, old_times, old_writes
            raise
    monkeypatch.setattr(Transaction, "commit", atomic_commit)
    def transactional(fn):
        def invoke(tx):
            result = fn(tx)
            tx.commit()
            return result
        return invoke
    monkeypatch.setattr(firestore, "transactional", transactional)
    root = version_path(BNY_EVENT, "a" * 64)
    state = {"status": "active", "generation": "a" * 64, "version_path": root, "event_sha256": digest(BNY_EVENT)}
    binding = BINDINGS["BNY"]
    db = DB({route_path(BNY_EVENT): state, root: {k: binding[k] for k in
             ("symbol", "cik", "currency", "issuer_id", "security_id")}})
    db.root = root
    monkeypatch.setattr(sm.storage, "get_db", lambda: db)
    return db


def test_atomic_create_rerun_and_old_ticker_fencing(db, evidence):
    doc = translated(evidence)
    planned = sm.commit_candidate(db, "BNY", doc)
    assert planned["action"] == "fill" and db.writes == 0
    applied = sm.commit_candidate(db, "BNY", doc, apply=True, now=NOW)
    assert db.writes == 2 and applied["path"].startswith(db.root)
    assert not any(p.startswith("tickers/") for p in db.data)
    doc["sec_provenance"]["source_capture"]["retrieved_at"] = NOW.isoformat()
    assert sm.commit_candidate(db, "BNY", doc, apply=True)["action"] == "noop"
    assert db.writes == 2
    with pytest.raises(IdentityError): sm.commit_candidate(db, "BK", doc, apply=True)


def test_interrupted_commit_has_no_partial_period_and_retries(db, evidence):
    db.fail_at = 2
    with pytest.raises(RuntimeError): sm.commit_candidate(db, "BNY", translated(evidence), apply=True)
    assert db.root + "/financials/2026-Q2" not in db.data and db.writes == 0
    db.fail_at = None
    assert sm.commit_candidate(db, "BNY", translated(evidence), apply=True)["action"] == "fill"


def test_conflicts_audited_without_overwrite_and_idempotent(db, evidence):
    doc = translated(evidence)
    target = db.root + "/financials/2026-Q2"
    old = {"period": "2026-Q2", "period_end": "2026-06-30", "symbol": "BNY", "income": {"Net Income": 0}}
    db.data[target] = deepcopy(old)
    assert sm.commit_candidate(db, "BNY", doc, apply=True)["action"] == "review"
    assert db.data[target] == old and db.writes == 1
    assert sm.commit_candidate(db, "BNY", doc, apply=True)["action"] == "review"
    assert db.writes == 1


def test_missing_route_and_different_security_reject_writes(db, evidence):
    db.data[db.root]["security_id"] = "different-share-class"
    with pytest.raises(IdentityError): sm.commit_candidate(db, "BNY", translated(evidence), apply=True)
    assert db.writes == 0


def test_rollback_requires_pause_and_stops_reapplication(db, evidence):
    doc = translated(evidence)
    sm.commit_candidate(db, "BNY", doc, apply=True, now=NOW)
    with pytest.raises(IdentityError): sm.rollback_candidate(db, "BNY", "2026-Q2", sm.candidate_key(doc))
    db.data[route_path(BNY_EVENT)]["status"] = "paused"
    assert sm.rollback_candidate(db, "BNY", "2026-Q2", sm.candidate_key(doc)) == "rolled_back_route_still_paused"
    assert db.root + "/financials/2026-Q2" not in db.data
    assert sm.rollback_candidate(db, "BNY", "2026-Q2", sm.candidate_key(doc)) == "already_rolled_back"
    db.data[route_path(BNY_EVENT)]["status"] = "active"
    assert sm.commit_candidate(db, "BNY", doc, apply=True)["action"] == "held_after_rollback"


def test_rollback_refuses_later_financial_observation(db, evidence):
    doc = translated(evidence)
    sm.commit_candidate(db, "BNY", doc, apply=True, now=NOW)
    db.data[route_path(BNY_EVENT)]["status"] = "paused"
    db.data[db.root + "/financials/2026-Q2"]["income"]["Net Income"] = 42
    with pytest.raises(IdentityError, match="changed"):
        sm.rollback_candidate(db, "BNY", "2026-Q2", sm.candidate_key(doc))


def test_off_mode_has_no_network_or_database_access(monkeypatch):
    monkeypatch.setattr(sm.storage, "get_db", lambda: pytest.fail("unexpected database access"))
    assert sm.run_fallback(["BNY"], mode="off") == ([], [])


def test_missing_yahoo_column_discovered_and_applied(db, evidence):
    import fetchers
    class Client:
        def get(self, path):
            return ((evidence["submissions"] if path.startswith("submissions") else evidence["facts"]),
                    evidence["source_capture"])
    # Q1 already populated, Q2 completely absent; no Yahoo column exists.
    q1 = deepcopy(translated(evidence))
    q1.update(period="2026-Q1", period_end="2026-03-31")
    db.data[db.root + "/financials/2026-Q1"] = q1
    results, failures = sm.run_fallback(["BNY"], yahoo_seen={"BNY": ([], [])}, client=Client(), db=db,
                                       mode="apply", now=NOW)
    assert not failures and any(r["status"] == "fill" for r in results)
    assert db.data[db.root + "/financials/2026-Q2"]["income"]["Diluted EPS"] == 2.45


def test_source_attribution_and_completeness(evidence):
    doc = translated(evidence)
    assert envelope.financial_upstream([doc]) == "SEC EDGAR"
    assert envelope.financial_upstream([doc, {"income": {}}]) == "SEC EDGAR, Yahoo Finance"
    completeness = envelope.completeness_block(doc)
    assert completeness["source"] == "sec_fallback" and completeness["status"] == "partial"
    import storage
    changed = deepcopy(doc)
    changed["income"]["Diluted Average Shares"] = 1
    merged, _ = storage.merge_financial_doc(doc, changed)
    assert merged["income"]["Diluted Average Shares"] == 692_223_000


def test_sec_http_throttling_retry_cache_and_user_agent():
    calls, sleeps = [], []
    class Response:
        def __init__(self, status, data=b'{"cik": 1}', headers=None):
            self.status_code, self.data, self.headers = status, data, headers or {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, size): yield self.data
    pending = [Response(429, headers={"Retry-After": "3"}), Response(200)]
    class Session:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return pending.pop(0)
    client = sf.SecClient(Session(), sleep=sleeps.append, monotonic=lambda: 0)
    first = client.get("submissions/CIK0000000001.json")
    assert client.get("submissions/CIK0000000001.json") == first
    assert len(calls) == 2 and sleeps == [3.0, 0.5]
    assert calls[0][1]["headers"]["User-Agent"] == "TEK2day Finance support@tek2day.com"
    assert first[1]["sha256"] and first[1]["retrieved_at"]
    with pytest.raises(IdentityError): client.get("https://other.invalid/arbitrary")


@pytest.mark.parametrize("status,retry", [(403, None), (429, "120"), (500, "-1")])
def test_sec_unavailable_not_cached_or_treated_as_data(status, retry):
    class Response:
        status_code = status
        headers = {"Retry-After": retry} if retry else {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
    client = sf.SecClient(SimpleNamespace(get=lambda *a, **k: Response()), sleep=lambda _: None)
    with pytest.raises(IdentityError): client.get("submissions/CIK0000000001.json")
    assert not client.cache


def test_mapping_or_network_failure_reaches_job_outcome(db):
    class Client:
        def get(self, path): raise RuntimeError("upstream unavailable")
    results, failures = sm.run_fallback(["BNY"], mode="observe", client=Client(), db=db, now=NOW)
    assert not results and "upstream unavailable" in failures[0]
    assert db.writes == 0


def test_incomplete_field_mapping_reported_not_fabricated(evidence):
    binding = deepcopy(BINDINGS["BNY"])
    binding["cash_bridges"] = {}
    reports, _ = sf.filings_from(evidence["submissions"], binding, NOW)
    with pytest.raises(IdentityError, match="required statement fields"):
        sf.build_candidate(evidence["facts"], evidence["source_capture"], binding, reports[-1], reports)


def test_sec_write_preserves_nested_history_and_original_ingestion(db, evidence):
    doc = translated(evidence)
    target = db.root + "/financials/2026-Q2"
    old = {"symbol": "BNY", "period": "2026-Q2", "period_end": "2026-06-30",
           "fetched_at": "2026-07-15T12:00:00+00:00", "income": {"Diluted EPS": 2.45}}
    db.data[target] = deepcopy(old)
    nested = target + "/revisions/r1/detail/sub"
    db.data[nested] = {"old_observation": 7}
    plan = sm.commit_candidate(db, "BNY", doc, apply=True, now=NOW)
    assert db.data[nested] == {"old_observation": 7}
    assert db.data[target]["fetched_at"] == old["fetched_at"]
    assert db.data[plan["audit_path"]]["original"] == old


def test_partial_statement_trigger_includes_missing_eps(evidence):
    doc = translated(evidence)
    doc["income"].pop("Diluted EPS")
    assert not sf.coverage_missing(doc)
    assert "income.Diluted EPS" in sf.mapped_missing(doc, BINDINGS["BNY"], "2026-06-30")


def test_adr_not_eligible_for_common_share_profile(evidence):
    binding = deepcopy(BINDINGS["BNY"])
    binding["is_adr"] = True
    reports, _ = sf.filings_from(evidence["submissions"], binding, NOW)
    with pytest.raises(IdentityError):
        sf.build_candidate(evidence["facts"], evidence["source_capture"], binding, reports[-1], reports)


def test_mapping_does_not_synthesize_missing_estimates_or_quotes(evidence):
    doc = translated(evidence)
    assert not any(k in doc for k in ("price", "quote", "estimates", "eps_avg", "provider_observed_at"))


def test_unknown_symbol_cannot_enroll_itself_by_cik(db):
    with pytest.raises(IdentityError): sm.checked_binding(db, "SAME-CIK")
    assert sm.run_fallback(["SAME-CIK"], mode="apply", db=db) == ([], [])


def test_scheduled_sec_failure_fails_even_empty_yahoo_tranche(monkeypatch):
    import pull_quarterly_financials as job
    monkeypatch.setattr(job.storage, "list_active_tickers", lambda: [])
    monkeypatch.setattr(sm, "run_fallback", lambda *a, **k: ([], ["BNY:SEC unavailable"]))
    monkeypatch.setattr(job, "_records", [])
    with pytest.raises(RuntimeError, match="maintenance incomplete"):
        job.main()


def test_primary_facts_do_not_refresh_observation_date_on_rerun(db, evidence):
    doc = translated(evidence)
    plan = sm.commit_candidate(db, "BNY", doc, apply=True, now=NOW)
    old = deepcopy(db.data[plan["path"]])
    doc["sec_provenance"]["source_capture"]["retrieved_at"] = (NOW + timedelta(hours=1)).isoformat()
    sm.commit_candidate(db, "BNY", doc, apply=True, now=NOW + timedelta(hours=1))
    assert db.data[plan["path"]] == old
