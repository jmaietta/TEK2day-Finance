"""Reviewed SEC mappings and issuer/security bindings; never discovered by CIK alone.

Adding an issuer requires review of its reporting basis, security and sources.
The engine supports 10-Q/10-K periods; initial live eligibility stays BNY-only.
"""
from security_identity import BNY_EVENT, digest

# Exact concepts, units and signs. No fuzzy field-name matching. A missing or
# ambiguous concept remains a gap. Units in Company Facts are already dollars
# or shares, unlike the millions/thousands printed in a filing PDF.
COMMON = {
    "income": {
        "Net Income": ("NetIncomeLoss", "USD", 1),
        "Tax Provision": ("IncomeTaxExpenseBenefit", "USD", 1),
    },
    "balance_sheet": {
        "Total Assets": ("Assets", "USD", 1),
        "Stockholders Equity": ("StockholdersEquity", "USD", 1),
        "Total Liabilities Net Minority Interest": ("Liabilities", "USD", 1),
        "Goodwill": ("Goodwill", "USD", 1),
        "Net PPE": ("PropertyPlantAndEquipmentNet", "USD", 1),
        "Retained Earnings": ("RetainedEarningsAccumulatedDeficit", "USD", 1),
    },
    "cash_flow": {
        "Operating Cash Flow": ("NetCashProvidedByUsedInOperatingActivities", "USD", 1),
        "Investing Cash Flow": ("NetCashProvidedByUsedInInvestingActivities", "USD", 1),
        "Financing Cash Flow": ("NetCashProvidedByUsedInFinancingActivities", "USD", 1),
        "Capital Expenditure": ("PaymentsToAcquirePropertyPlantAndEquipment", "USD", -1),
    },
}

PROFILES = {
    "us-gaap-bank-bny-v1": {
        "review": "BNY Q1/Q2 2026 statements and stored Q1 field definitions compared September 10, 2026",
        "revenue_concepts": ["NoninterestIncome", "InterestIncomeExpenseNet"],
        "income": {
            "Net Income Including Noncontrolling Interests": ("ProfitLoss", "USD", 1),
            "Net Income Common Stockholders": ("NetIncomeLossAvailableToCommonStockholdersBasic", "USD", 1),
            "Net Interest Income": ("InterestIncomeExpenseNet", "USD", 1),
            "Interest Income": ("InterestAndDividendIncomeOperating", "USD", 1),
            "Interest Expense": ("InterestExpenseOperating", "USD", 1),
        },
        "balance_sheet": {"Cash Financial": ("CashAndDueFromBanks", "USD", 1)},
        "balance_formulas": {
            "Minority Interest": ["MinorityInterest", "TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests"],
            "Total Equity Gross Minority Interest": ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                                                     "TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests"],
        },
        "cash_flow": {},
        "limitations": ["Bank cash differs from Yahoo Cash And Cash Equivalents",
                       "Debt, staff expense and intangible definitions require further mapping review"],
    },
    # Reusable industrial profile, deliberately no issuer automatically enrolled.
    "us-gaap-industrial-v1": {
        "review": "Standard consolidated US-GAAP concepts; issuer enrollment still requires definition review",
        "revenue_concepts": ["RevenueFromContractWithCustomerExcludingAssessedTax"],
        "income": {}, "balance_sheet": {}, "cash_flow": {},
        "limitations": ["Custom-taxonomy and sector-specific concepts require a reviewed profile"],
    },
}

BNY_Q2_URL = "https://www.bny.com/content/dam/bnymellon/documents/pdf/investor-relations/form-10-q-2q26.pdf"
BNY_Q1_URL = "https://www.bny.com/assets/corporate/documents/pdf/investor-relations/form-10-q-1q26-final.pdf"
BINDINGS = {
    "BNY": {
        "symbol": "BNY", "cik": "0001390777", "currency": "USD",
        "issuer_id": BNY_EVENT["issuer_id"], "security_id": BNY_EVENT["security_id"],
        "identity_event_sha256": digest(BNY_EVENT), "profile": "us-gaap-bank-bny-v1",
        "share_class": "common", "exchange": "NYSE", "is_adr": False,
        "periods_from": "2026-01-01",
        "evidence": [source["url"] for source in BNY_EVENT["sources"]],
        # Filing-based shares/EPS may differ from split-adjusted Yahoo history.
        # Automatic per-share insertion is limited to this explicitly reviewed
        # period. Other periods can still recover issuer-level statements.
        "per_share_periods": ["2026-06-30"],
        "cash_bridges": {
            "2026-06-30": {
                "current_accession": "0001390777-26-000086", "prior_accession": "0001390777-26-000060",
                "prior_end": "2026-03-31", "ytd_start": "2026-01-01",
                "basis_review": "Same consolidated cash-flow basis in Q1 and Q2; six-month YTD less Q1",
                "sources": [{"url": BNY_Q2_URL, "page": 50,
                             "sha256": "7e004391586962b2d0ff73c43d540f88b45bc80052b4099b4dd65a99fe2dce2d"},
                            {"url": BNY_Q1_URL, "page": 48,
                             "sha256": "ba5624c10f0e70b7fea1b71a3fb32ed040fcf671eaa2156213cfa518cb45f63c"}],
            },
        },
    },
}
