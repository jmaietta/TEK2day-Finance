"""SEC financial fallback: discovery, exact-period translation and gap planning.

Pure translation is separate from network and storage. No work runs on import.
No current quote, estimate, alias, or corporate-action inference is made here.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import re
import time

from security_identity import digest, require
from sec_mapping import COMMON, PROFILES

FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A"}
SECTIONS = ("income", "balance_sheet", "cash_flow")
REQUIRED = {"income": ("Total Revenue", "Net Income"), "balance_sheet": ("Total Assets",),
            "cash_flow": ("Operating Cash Flow",)}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class SecClient:
    """One sequential job client, <=2 requests/s, bounded retries and memory.

    Cache lasts only for this job. Never cache errors or persist stale API bodies.
    No archive traversal/bulk downloads. A recent-history truncation is explicit.
    """
    def __init__(self, session=None, sleep=time.sleep, monotonic=time.monotonic):
        import requests
        self.session = session or requests.Session()
        self.sleep, self.clock = sleep, monotonic
        self.last_request = None
        self.cache = {}

    def get(self, path):
        require(bool(re.fullmatch(r"(?:submissions/CIK\d{10}|api/xbrl/companyfacts/CIK\d{10})\.json", path)),
                "Unsupported SEC URL")
        if path in self.cache:
            return self.cache[path]
        url = "https://data.sec.gov/" + path
        for attempt in range(3):
            if self.last_request is not None:
                self.sleep(max(0, 0.5 - (self.clock() - self.last_request)))
            self.last_request = self.clock()
            # Stream so an unexpectedly large response cannot exhaust a worker.
            with self.session.get(url, headers={"User-Agent": "TEK2day Finance support@tek2day.com",
                                               "Accept": "application/json"},
                                  timeout=(10, 40), stream=True, allow_redirects=False) as response:
                if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    delay = 2 ** (attempt + 1)
                    retry = response.headers.get("Retry-After")
                    if retry:
                        try:
                            delay = float(retry)
                        except ValueError:
                            delay = (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds()
                    # Do not retry earlier than SEC asked; leave long waits for
                    # the next scheduled run, where existing job alerts apply.
                    require(0 <= delay <= 60, "SEC Retry-After exceeds bounded job retry; defer")
                    self.sleep(delay)
                    continue
                require(response.status_code == 200, f"SEC HTTP {response.status_code}")
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    require(size <= 25_000_000, "SEC response exceeds 25 MB bound")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                body = json.loads(raw)
                require(isinstance(body, dict), "Invalid SEC JSON document")
                result = (body, {"url": url, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                                 "sha256": hashlib.sha256(raw).hexdigest(), "bytes": size})
                if len(self.cache) >= 16:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[path] = result
                return result
        raise RuntimeError("SEC retries exhausted")


def filings_from(submissions, binding, now):
    """Discover reports even when Yahoo never created a period column.

    Use supplied acceptance time for 168 hours. For date-only evidence, the
    deadline is a conservative upper bound (end of filing day at UTC-06, plus
    seven days), not an invented filing timestamp. Run on the next job tick.
    """
    require(now.tzinfo is not None, "UTC-aware current time required")
    require(str(submissions.get("cik")).zfill(10) == binding["cik"], "SEC CIK mismatch")
    symbols = [str(s).upper().replace("-", ".") for s in submissions.get("tickers", [])]
    wanted = binding["symbol"].replace("-", ".")
    require(symbols.count(wanted) == 1, "SEC listing mismatch or ambiguous ticker")
    exchanges = submissions.get("exchanges", [])
    require(len(exchanges) == len(symbols) and exchanges[symbols.index(wanted)] == binding["exchange"],
            "SEC listing venue mismatch")
    recent = submissions.get("filings", {}).get("recent", {})
    keys = ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument")
    require(all(isinstance(recent.get(k), list) for k in keys), "Incomplete SEC submissions arrays")
    require(len({len(recent[k]) for k in keys}) == 1 and len(recent[keys[0]]) <= 5000,
            "Malformed or oversized submissions arrays")
    reports, notices = [], []
    for i, form in enumerate(recent["form"]):
        if form not in FORMS:
            continue
        row = {k: recent[k][i] for k in keys}
        filed, end = date.fromisoformat(row["filingDate"]), date.fromisoformat(row["reportDate"])
        if end < date.fromisoformat(binding["periods_from"]):
            continue
        require(end <= filed <= now.date(), "Invalid SEC filing/report date")
        require(re.fullmatch(r"\d{10}-\d{2}-\d{6}", row["accessionNumber"]) is not None,
                "Invalid SEC accession")
        require(re.fullmatch(r"[A-Za-z0-9_.-]+", row["primaryDocument"]) is not None,
                "Invalid SEC primary document")
        accepted = recent.get("acceptanceDateTime", [])
        supplied = accepted[i] if i < len(accepted) else None
        if supplied:
            instant = datetime.fromisoformat(supplied.replace("Z", "+00:00"))
            require(instant.tzinfo is not None and instant <= now and abs((instant.date() - filed).days) <= 1,
                    "Invalid SEC acceptance timestamp")
            deadline = instant + timedelta(days=7)
            row["accepted_at"] = supplied
            row["filing_date_precision"] = "timestamp"
        else:
            deadline = datetime(filed.year, filed.month, filed.day, 6, tzinfo=timezone.utc) + timedelta(days=8)
            row["accepted_at"] = None
            row["filing_date_precision"] = "day"
        row["eligible_after"] = deadline.isoformat()
        row["eligible"] = now >= deadline
        row["source_url"] = (f"https://www.sec.gov/Archives/edgar/data/{int(binding['cik'])}/"
                             + row["accessionNumber"].replace("-", "") + "/" + row["primaryDocument"])
        reports.append(row)
    # Any amendment for a report (even one still in its grace period) prevents
    # silently selecting the original. Neither latest-wins nor merged filings.
    grouped = {}
    for row in reports:
        grouped.setdefault((row["form"].replace("/A", ""), row["reportDate"]), []).append(row)
    selected = []
    for group in grouped.values():
        if len(group) != 1 or group[0]["form"].endswith("/A"):
            notices.append({"status": "review", "reason": "amended_or_multiple_filings", "filings": group})
        elif group[0]["eligible"]:
            selected.append(group[0])
    if submissions.get("filings", {}).get("files"):
        notices.append({"status": "scope", "reason": "recent_submission_history_only; older archives not scanned"})
    require(len(selected) <= 16, "More than 16 eligible reports; narrow reviewed binding period scope")
    return sorted(selected, key=lambda r: (r["reportDate"], r["form"])), notices


def observations(facts, tag, unit, filing, start, end):
    values = facts.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {}).get(unit, [])
    matched = [v for v in values if v.get("accn") == filing["accessionNumber"]
               and v.get("start") == start and v.get("end") == end]
    for value in matched:
        require(value.get("form") == filing["form"] and value.get("filed") == filing["filingDate"],
                f"Conflicting filing metadata for {tag}")
        require(not any(k in value for k in ("segment", "dimensions")), f"Nonconsolidated context: {tag}")
        require(finite(value.get("val")), f"Nonfinite SEC fact: {tag}")
    require(len({v["val"] for v in matched}) <= 1, f"Conflicting facts: {tag} {start}/{end}")
    if not matched:
        return None
    return {"value": matched[0]["val"], "concept": "us-gaap:" + tag, "unit": unit,
            "observations": sorted(deepcopy(matched), key=digest), "source_url": filing["source_url"]}


def reporting_start(facts, filing):
    """Select exact annual/quarter duration; `fp` labels the filing, not each fact."""
    end = date.fromisoformat(filing["reportDate"])
    low, high = (330, 400) if filing["form"] == "10-K" else (70, 105)
    values = facts.get("facts", {}).get("us-gaap", {}).get("NetIncomeLoss", {}).get("units", {}).get("USD", [])
    starts = set()
    for v in values:
        if v.get("accn") == filing["accessionNumber"] and v.get("end") == end.isoformat() and v.get("start"):
            duration = (end - date.fromisoformat(v["start"])).days + 1
            if low <= duration <= high:
                starts.add(v["start"])
    require(len(starts) == 1, "Missing/ambiguous reporting duration; transitional filings need review")
    return starts.pop()


def build_candidate(facts, receipt, binding, filing, all_filings):
    require(str(facts.get("cik")).zfill(10) == binding["cik"], "Company Facts registrant mismatch")
    require(binding["profile"] in PROFILES and binding["currency"] == "USD"
            and binding.get("evidence") and not binding.get("is_adr"), "Unreviewed mapping/security basis")
    profile = PROFILES[binding["profile"]]
    end, start = filing["reportDate"], reporting_start(facts, filing)
    require(start >= binding["periods_from"], "Reporting period precedes reviewed issuer/security binding")
    annual = filing["form"] == "10-K"
    # Existing repository IDs use the calendar bucket of the actual end date.
    # Fiscal period dates remain explicit; NVDA July is not called fiscal Q3.
    d = date.fromisoformat(end)
    period = f"{d.year}-FY" if annual else f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    doc = {"symbol": binding["symbol"], "period": period, "period_start": start,
           "period_end": end, "freq": "FY" if annual else "Q", "currency": binding["currency"],
           "issuer_id": binding["issuer_id"], "security_id": binding["security_id"],
           "cik": binding["cik"], **{s: {} for s in SECTIONS}}
    lineage, gaps = {}, []

    def read(tag, unit="USD", begin=start, final=end, report=filing):
        return observations(facts, tag, unit, report, begin, final)

    def put(section, field, evidence, sign=1):
        key = section + "." + field
        if evidence is None:
            gaps.append(key)
        else:
            doc[section][field] = sign * evidence["value"]
            lineage[key] = {**evidence, "sign": sign}

    bridge = binding.get("cash_bridges", {}).get(end)

    def cash(tag, unit):
        direct = read(tag, unit)
        if direct or annual:
            return direct
        if not bridge or bridge["current_accession"] != filing["accessionNumber"]:
            return None
        require(bridge.get("basis_review") and bridge.get("sources"), "Unreviewed cross-filing cash-flow basis")
        prior = [f for f in all_filings if f["accessionNumber"] == bridge["prior_accession"]]
        require(len(prior) == 1, "Prior cash-flow filing missing, ineligible or amended")
        require((date.fromisoformat(start) - date.fromisoformat(bridge["prior_end"])).days == 1,
                "Noncontiguous cash-flow bridge")
        before = read(tag, unit, bridge["ytd_start"], bridge["prior_end"], prior[0])
        after = read(tag, unit, bridge["ytd_start"])
        if before is None or after is None:
            return None
        return {"value": after["value"] - before["value"], "formula": "YTD minus prior YTD",
                "unit": unit, "terms": [after, before], "basis_review": bridge}

    for section in SECTIONS:
        mapping = {**COMMON[section], **profile[section]}
        for field, (tag, unit, sign) in mapping.items():
            value = cash(tag, unit) if section == "cash_flow" else read(tag, unit, None if section == "balance_sheet" else start)
            put(section, field, value, sign)
    terms = [read(tag) for tag in profile["revenue_concepts"]]
    revenue = None if any(v is None for v in terms) else {"value": sum(v["value"] for v in terms),
              "formula": " + ".join(profile["revenue_concepts"]), "unit": "USD", "terms": terms}
    put("income", "Total Revenue", revenue)
    for field, tags in profile.get("balance_formulas", {}).items():
        terms = [read(tag, begin=None) for tag in tags]
        value = None if any(v is None for v in terms) else {"value": sum(v["value"] for v in terms),
                 "formula": " + ".join(tags), "unit": "USD", "terms": terms}
        put("balance_sheet", field, value)
    for section, formulas in profile.get("statement_formulas", {}).items():
        require(section in SECTIONS, "Unknown formula statement")
        for field, specifications in formulas.items():
            require(specifications and all(sign in (-1, 1) for _, sign in specifications),
                    "Invalid reviewed formula")
            terms = []
            for tag, sign in specifications:
                term = cash(tag, "USD") if section == "cash_flow" else read(tag, begin=None if section == "balance_sheet" else start)
                terms.append(None if term is None else {**term, "coefficient": sign})
            value = None if any(t is None for t in terms) else {
                "value": sum(t["coefficient"] * t["value"] for t in terms),
                "formula": "signed sum of reviewed SEC concepts", "unit": "USD", "terms": terms}
            put(section, field, value)
    cf = doc["cash_flow"]
    if all(finite(cf.get(f)) for f in ("Operating Cash Flow", "Capital Expenditure")):
        put("cash_flow", "Free Cash Flow", {"value": cf["Operating Cash Flow"] + cf["Capital Expenditure"],
            "formula": "Operating Cash Flow + signed Capital Expenditure", "unit": "USD",
            "terms": [lineage["cash_flow.Operating Cash Flow"], lineage["cash_flow.Capital Expenditure"]]})
    if end in binding.get("per_share_periods", []):
        require(binding.get("share_class") == "common", "Per-share class not reviewed")
        for field, tag, unit in [("Diluted EPS", "EarningsPerShareDiluted", "USD/shares"),
                                 ("Basic EPS", "EarningsPerShareBasic", "USD/shares"),
                                 ("Diluted Average Shares", "WeightedAverageNumberOfDilutedSharesOutstanding", "shares"),
                                 ("Basic Average Shares", "WeightedAverageNumberOfSharesOutstandingBasic", "shares")]:
            put("income", field, read(tag, unit))
    critical = [s + "." + f for s, fields in REQUIRED.items() for f in fields if not finite(doc[s].get(f))]
    require(not critical, "SEC cannot supply required statement fields: " + ", ".join(critical))
    # Restrict arithmetic validation to quantities whose definitions are matched.
    for field in ("Total Assets",):
        require(doc["balance_sheet"][field] >= 0, "Negative total assets")
    bs = doc["balance_sheet"]
    if all(finite(bs.get(f)) for f in ("Total Assets", "Total Liabilities Net Minority Interest", "Total Equity Gross Minority Interest")):
        require(abs(bs["Total Assets"] - bs["Total Liabilities Net Minority Interest"] - bs["Total Equity Gross Minority Interest"])
                <= 1_000_000, "SEC balance sheet does not reconcile within disclosed USD-million rounding")
    doc["sec_provenance"] = {"profile": binding["profile"], "binding_sha256": digest(binding),
        "filing": deepcopy(filing), "source_capture": deepcopy(receipt), "fields": lineage,
        "unmapped_or_missing": gaps, "limitations": profile["limitations"],
        "per_share_basis": "as filed; only explicitly reviewed periods"}
    if profile.get("mapping_evidence"):
        doc["sec_provenance"]["mapping_evidence"] = deepcopy(profile["mapping_evidence"])
    return doc


def merge_candidate(existing, incoming):
    """No populated overwrite, including shares. Every conflicting fact is kept."""
    if existing is None:
        return deepcopy(incoming), sorted(incoming["sec_provenance"]["fields"]), []
    require(all(existing.get(k) == incoming[k] for k in ("symbol", "period", "period_end")),
            "Period/symbol collision; refusing financial join")
    for key in ("freq", "period_start", "currency", "cik", "issuer_id", "security_id"):
        if existing.get(key) is not None:
            held = str(existing[key]).zfill(10) if key == "cik" else existing[key]
            require(held == incoming[key], f"Existing period identity differs: {key}")
    merged, filled, conflicts = deepcopy(existing), [], []
    for section in SECTIONS:
        require(existing.get(section) is None or isinstance(existing[section], dict), "Malformed stored statement")
        if merged.get(section) is None:
            merged[section] = {}
        for field, value in incoming[section].items():
            old = merged[section].get(field)
            key = section + "." + field
            if old is None or (isinstance(old, float) and not math.isfinite(old)):
                merged[section][field] = value
                filled.append(key)
            elif not finite(old) or old != value:
                conflicts.append({"field": key, "selected": "existing", "existing": old, "sec": value})
    if filled:
        provenance = deepcopy(incoming["sec_provenance"])
        provenance["selected_fields"] = sorted(filled)
        merged.setdefault("sec_backfills", {})[digest(provenance)] = provenance
    return merged, sorted(filled), conflicts


def coverage_missing(doc):
    return [s + "." + f for s, fields in REQUIRED.items() for f in fields
            if not finite(((doc or {}).get(s) or {}).get(f))]


def mapped_missing(doc, binding, end):
    """Trigger for partial statements as well as missing headline anchors."""
    profile = PROFILES[binding["profile"]]
    expected = {s: set(COMMON[s]) | set(profile[s]) for s in SECTIONS}
    expected["income"].add("Total Revenue")
    expected["balance_sheet"].update(profile.get("balance_formulas", {}))
    for section, formulas in profile.get("statement_formulas", {}).items():
        expected[section].update(formulas)
    expected["cash_flow"].add("Free Cash Flow")
    if end in binding.get("per_share_periods", []):
        expected["income"].update({"Basic EPS", "Diluted EPS", "Basic Average Shares", "Diluted Average Shares"})
    return [s + "." + f for s in SECTIONS for f in sorted(expected[s])
            if not finite(((doc or {}).get(s) or {}).get(f))]
