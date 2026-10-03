"""Sector from a company's SEC industry code (SIC), for names Yahoo cannot classify.

Owner, 2026-10-02: "Think about how we could collect sector data for foreign
firms" ... "let's fucken build them please" ... "I am not paying a vendor."

Every company that files with the SEC, foreign filers of 20-F and 40-F reports
included, carries a four-digit SIC code in its SEC record. The map from code to
sector (sec_sic_map.py) is measured, not guessed: on October 3, 2026 every
TEK2day company with both a Yahoo sector and a CIK was matched to its SIC code,
and each code takes the sector most of its companies have. A code with too few
companies, or no clear majority, falls back to its two-digit group. The
industry is the SEC's own description of the code.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("ydp.sec_sectors")
USER_AGENT = "TEK2day Finance support@tek2day.com"


def sector_for_sic(sic: str) -> str:
    """The Yahoo sector for an SEC industry code: the owner's call, else the measured code, else its group."""
    from sec_sic_map import CODES, GROUPS, OWNER_CODES
    sic = str(sic or "").strip()
    if not sic:
        return ""
    return OWNER_CODES.get(sic) or CODES.get(sic) or GROUPS.get(sic[:2]) or ""


def fetch_sic(cik: int) -> tuple[str, str]:
    """(SIC code, the SEC's description of it) for a CIK; empty strings if unavailable. One SEC request."""
    import requests
    try:
        r = requests.get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json",
                         headers={"User-Agent": USER_AGENT}, timeout=30)
        r.raise_for_status()
        body = r.json()
        return str(body.get("sic") or ""), str(body.get("sicDescription") or "")
    except Exception as exc:
        logger.warning("SIC lookup failed for CIK %s: %s", cik, exc)
        return "", ""


def sector_from_sec(cik) -> tuple[str, str] | None:
    """(sector, industry) from the SEC industry code, or None."""
    if not cik:
        return None
    sic, description = fetch_sic(int(cik))
    sector = sector_for_sic(sic)
    if not sector:
        return None
    return sector, description.title() if description else ""
