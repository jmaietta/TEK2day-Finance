"""Offline, reviewed SEC Company Facts adapter. No network or database writes.

CIK locates filings; a reviewed security binding authorizes their use. The first
profile is deliberately bounded to BNY's 2026 Q2 common-share statements.
Other issuers, reporting calendars and accounting bases require profile review.
"""
from copy import deepcopy
from datetime import date, datetime, timezone
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from security_identity import BNY_EVENT, digest, require, validate_event
from ticker_migration import missing

PROFILE = "bny-common-2026-q2-v1"
ACCESSION = "0001390777-26-000086"
PRIOR_ACCESSION = "0001390777-26-000060"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK0001390777.json"
Q2_URL = "https://www.bny.com/content/dam/bnymellon/documents/pdf/investor-relations/form-10-q-2q26.pdf"
Q1_URL = "https://www.bny.com/assets/corporate/documents/pdf/investor-relations/form-10-q-1q26-final.pdf"
SOURCES = [
    {"url": Q2_URL, "sha256": "7e004391586962b2d0ff73c43d540f88b45bc80052b4099b4dd65a99fe2dce2d",
     "pages": [47, 49, 50], "report_date": "2026-06-30", "filed": "2026-07-31"},
    {"url": Q1_URL, "sha256": "ba5624c10f0e70b7fea1b71a3fb32ed040fcf671eaa2156213cfa518cb45f63c",
     "pages": [48], "report_date": "2026-03-31", "filed": "2026-05-01"},
]

# section -> Yahoo-compatible field -> exact reviewed US-GAAP concept.
# No fuzzy labels, ordered synonym guesses, or sector-wide automatic mappings.
INCOME = {
    "Net Income": "NetIncomeLoss",  # Parent, after noncontrolling interests.
    "Net Income Including Noncontrolling Interests": "ProfitLoss",
    "Net Income Common Stockholders": "NetIncomeLossAvailableToCommonStockholdersBasic",
    "Preferred Stock Dividends": "PreferredStockDividendsIncomeStatementImpact",
    "Interest Income": "InterestAndDividendIncomeOperating",
    "Interest Expense": "InterestExpenseOperating",
    "Net Interest Income": "InterestIncomeExpenseNet",
    "Tax Provision": "IncomeTaxExpenseBenefit",
    "Professional Expense And Contract Services Expense": "ProfessionalFees",
}
PER_SHARE = {"Basic EPS": "EarningsPerShareBasic", "Diluted EPS": "EarningsPerShareDiluted"}
SHARES = {"Basic Average Shares": "WeightedAverageNumberOfSharesOutstandingBasic",
          "Diluted Average Shares": "WeightedAverageNumberOfDilutedSharesOutstanding"}
BALANCE = {
    "Total Assets": "Assets",
    "Stockholders Equity": "StockholdersEquity",
    "Total Liabilities Net Minority Interest": "Liabilities",
    "Cash Financial": "CashAndDueFromBanks",
    "Goodwill": "Goodwill",
    "Net PPE": "PropertyPlantAndEquipmentNet",
    "Commercial Paper": "CommercialPaper",
    "Preferred Stock": "PreferredStockValue",
    "Retained Earnings": "RetainedEarningsAccumulatedDeficit",
    "Treasury Stock": "TreasuryStockValue",
    "Additional Paid In Capital": "AdditionalPaidInCapital",
}
# Only additive cash-flow concepts permit YTD subtraction. EPS/share averages
# never do. SEC payment concepts are positive magnitudes; Yahoo uses outflows.
CASH = {
    "Operating Cash Flow": ("NetCashProvidedByUsedInOperatingActivities", 1),
    "Investing Cash Flow": ("NetCashProvidedByUsedInInvestingActivities", 1),
    "Financing Cash Flow": ("NetCashProvidedByUsedInFinancingActivities", 1),
    "Capital Expenditure": ("PaymentsToAcquirePropertyPlantAndEquipment", -1),
    "Cash Dividends Paid": ("PaymentsOfDividends", -1),
    "Effect Of Exchange Rate Changes": ("EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", 1),
}
CASH_POSITION = "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"


def select_fact(facts, tag, unit, accession, start, end):
    """Select one explicit observation; preserve duplicates, refuse conflicts.

    No latest-filing heuristic: later restatements and amendments are separate
    observations. `fy`, `fp` and `frame` cannot substitute for duration dates.
    """
    observations = facts.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {}).get(unit, [])
    matches = [v for v in observations if v.get("accn") == accession
               and v.get("start") == start and v.get("end") == end
               and v.get("form") == "10-Q"]
    require(bool(matches), f"Missing exact fact: {tag} {unit} {start}/{end} {accession}")
    expected_filed = {ACCESSION: "2026-07-31", PRIOR_ACCESSION: "2026-05-01"}.get(accession)
    for v in matches:
        value = v.get("val")
        require(type(value) in (int, float) and math.isfinite(value), f"Invalid fact: {tag}")
        require(v.get("filed") == expected_filed and not any(k in v for k in ("segment", "dimensions")),
                f"Unreviewed filing/context: {tag}")
    require(len({v["val"] for v in matches}) == 1, f"Conflicting exact facts: {tag}")
    return matches[0]["val"], {"taxonomy": "us-gaap", "concept": tag, "unit": unit,
                               "observations": sorted(deepcopy(matches), key=digest)}


def candidate(facts, receipt, *, event=BNY_EVENT):
    """Build a candidate, never a write instruction or completeness assertion."""
    validate_event(event)
    require(digest(event) == digest(BNY_EVENT), "No reviewed SEC mapping for this security/event")
    require(str(facts.get("cik")).zfill(10) == event["to"]["cik"], "SEC registrant mismatch")
    require(receipt.get("url") == FACTS_URL and len(receipt.get("sha256", "")) == 64,
            "Source capture receipt required")
    retrieved = datetime.fromisoformat(receipt["retrieved_at"])
    require(retrieved.tzinfo is not None and retrieved.date() >= date(2026, 7, 31)
            and retrieved <= datetime.now(timezone.utc), "Invalid source retrieval time")
    doc = {"symbol": "BNY", "period": "2026-Q2", "period_start": "2026-04-01",
           "period_end": "2026-06-30", "freq": "Q", "currency": "USD",
           "issuer_id": event["issuer_id"], "security_id": event["security_id"],
           "cik": event["to"]["cik"], "income": {}, "balance_sheet": {}, "cash_flow": {}}
    lineage = {}

    def read(tag, *, unit="USD", accession=ACCESSION, start="2026-04-01", end="2026-06-30"):
        return select_fact(facts, tag, unit, accession, start, end)

    def put(section, field, observation):
        value, evidence = observation
        doc[section][field] = value
        lineage[f"{section}.{field}"] = evidence

    for field, tag in INCOME.items():
        put("income", field, read(tag))
    for field, tag in PER_SHARE.items():
        put("income", field, read(tag, unit="USD/shares"))
    for field, tag in SHARES.items():
        put("income", field, read(tag, unit="shares"))
    for field, tag in BALANCE.items():
        put("balance_sheet", field, read(tag, start=None))

    def calculate(section, field, terms, formula):
        put(section, field, (sum(sign * v for sign, (v, _) in terms),
                             {"formula": formula, "terms": [{"coefficient": sign, **ev}
                              for sign, (_, ev) in terms]}))

    calculate("income", "Total Revenue", [(1, read("NoninterestIncome")), (1, read("InterestIncomeExpenseNet"))],
              "noninterest income + net interest income (bank revenue)")
    calculate("income", "Pretax Income", [(1, read("ProfitLoss")), (1, read("IncomeTaxExpenseBenefit"))],
              "consolidated net income + income tax expense")
    temporary = read("TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests", start=None)
    calculate("balance_sheet", "Total Equity Gross Minority Interest",
              [(1, read("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", start=None)), (1, temporary)],
              "permanent equity + temporary equity; existing Yahoo basis includes both")
    calculate("balance_sheet", "Minority Interest", [(1, read("MinorityInterest", start=None)), (1, temporary)],
              "nonredeemable + redeemable noncontrolling interests; existing Yahoo basis includes both")
    # Reviewed Q1/Q2 consolidated statements use the same cash-flow basis. This
    # assertion is profile-specific, not inferred merely from matching concepts.
    for field, (tag, sign) in CASH.items():
        ytd = read(tag, start="2026-01-01")
        q1 = read(tag, accession=PRIOR_ACCESSION, start="2026-01-01", end="2026-03-31")
        calculate("cash_flow", field, [(sign, ytd), (-sign, q1)],
                  f"{sign} * (2026 six-month YTD - 2026 Q1); same consolidated basis reviewed in both PDFs")
    put("cash_flow", "Beginning Cash Position", read(CASH_POSITION, accession=PRIOR_ACCESSION,
                                                    start=None, end="2026-03-31"))
    put("cash_flow", "End Cash Position", read(CASH_POSITION, start=None))
    cf = doc["cash_flow"]
    require(cf["End Cash Position"] - cf["Beginning Cash Position"] == sum(cf[f] for f in
            ("Operating Cash Flow", "Investing Cash Flow", "Financing Cash Flow", "Effect Of Exchange Rate Changes")),
            "Quarterly cash-flow roll-forward failed")
    bs = doc["balance_sheet"]
    require(bs["Total Assets"] == bs["Total Liabilities Net Minority Interest"] + bs["Total Equity Gross Minority Interest"],
            "Balance sheet does not reconcile")
    doc["sec_provenance"] = {"profile": PROFILE, "event_sha256": digest(event),
        "capture": deepcopy(receipt), "filings": deepcopy(SOURCES), "fields": lineage,
        "coverage": "reviewed mapped fields only; not the full Yahoo schema",
        "limitations": ["No generic Cash And Cash Equivalents mapping: bank definitions differ",
                        "Staff expense, long-term debt and intangibles excluded: Q1 provider definitions differ",
                        "No automatic split adjustment of filed EPS/share counts",
                        "No quotes or consensus estimates supplied by Company Facts",
                        "Future amendments, restatements and reporting periods require review"]}
    return doc


def fill_gaps(existing, incoming):
    """Keep populated values (including zero/share counts); retain conflicts."""
    if existing is None:
        return deepcopy(incoming), sorted(incoming["sec_provenance"]["fields"]), []
    require(existing.get("symbol") == incoming["symbol"] and existing.get("period") == incoming["period"]
            and existing.get("period_end") == incoming["period_end"]
            and existing.get("freq", "Q") == "Q", "Stored financial period identity mismatch")
    for key in ("cik", "issuer_id", "security_id", "currency", "period_start"):
        if existing.get(key) is not None:
            require(existing[key] == incoming[key], f"Stored financial identity mismatch: {key}")
    merged, filled, conflicts = deepcopy(existing), [], []
    for section in ("income", "balance_sheet", "cash_flow"):
        require(existing.get(section) is None or isinstance(existing[section], dict), "Malformed stored statement")
        if merged.get(section) is None:
            merged[section] = {}
        for field, value in incoming[section].items():
            old = merged[section].get(field)
            path = f"{section}.{field}"
            if missing(old):
                merged[section][field] = value
                filled.append(path)
            elif old != value:
                conflicts.append({"field": path, "selected": "existing", "existing": old,
                                  "sec_candidate": value, "review_required": True})
    # Each source applies only to the fields actually filled. Complete candidate
    # and conflicting observations belong in the maintenance audit as well.
    if filled:
        provenance = deepcopy(incoming["sec_provenance"])
        provenance["selected_fields"] = sorted(filled)
        prior = merged.setdefault("sec_backfills", {})
        prior[digest(provenance)] = provenance
    return merged, sorted(filled), conflicts
