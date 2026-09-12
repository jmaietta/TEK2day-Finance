# XOM: registrant succession, not a ticker rename

Verified from primary public evidence September 10, 2026, in response to the
question about CIK changes. This is an evidence/design note, not an enabled
identity binding, database migration, or authorization for an XOM repair.

Update September 12: the bounded diagnosis, local successor implementation,
tests and exact metadata repair manifest are now prepared. See
[implementation and rollout](xom-succession-implementation.md). This preflight
records the earlier evidence baseline; the new correction is not yet deployed.

| Item | Predecessor | Successor |
| --- | --- | --- |
| Registrant | Exxon Mobil Corporation, New Jersey | ExxonMobil Holdings Corporation, Texas |
| CIK | 0000034088 | 0002115436 |
| Common-stock CUSIP | 30231G102 | 30233Q108 |
| Common-stock par value | No par value | $0.001 |
| Public ticker | XOM | XOM |
| Common-share exchange | One outstanding old share | One new share |

The redomiciliation merger became effective **July 1, 2026**. Its 8-K12B
identifies the new parent as successor registrant under Exchange Act Rule
12g-3(a). The old company continues as primary obligor on its notes; the new
holding company guarantees them. This is not a replacement of the old entity's
CIK. Both registrants have continuing meaning for their respective filings and
securities.

Sources:

- [Successor 8-K12B](https://www.sec.gov/Archives/edgar/data/2115436/000119312526291990/d71068d8k12b.htm),
  report/signature date July 1, 2026. Explanatory Note and Item 2.01 establish
  the successor, effective date and one-for-one common-share exchange; Item
  1.01 distinguishes the continuing old debt obligor from its new guarantor.
  It expected new common shares to begin trading July 2. An exact effective
  instant or actual first-trade timestamp has not been established here.
- [NYSE removal notice](https://www.sec.gov/Archives/edgar/data/34088/000087666126000593/ruleprovisionnotice.htm)
  identifies the old and new CUSIPs and explicitly limits the removal to the
  old common shares. The July 13 removal date is not the merger effective date.
- [June 2026 10-Q, Explanatory Note and Note 1](https://investor.exxonmobil.com/sec-filings/all-sec-filings/content/0000034088-26-000093/xom-20260630.htm)
  states that both registrants separately filed the report and that the merger
  did not change the consolidated financial reporting basis. This supports a
  reviewed financial continuity relationship, not indiscriminate concatenation.
- SEC submissions confirm the registrant IDs:
  [predecessor](https://data.sec.gov/submissions/CIK0000034088.json), body SHA-256
  `7918818ea3045fb09fbb18d67537909a28feb245d693d0bd7cd3bc30b16ea3db`;
  [successor](https://data.sec.gov/submissions/CIK0002115436.json), body SHA-256
  `c8aee4327ac005e1db4dc0294f3af5a40300a1a06e373df76af16b3bec260db8`.
  At this check, the predecessor's ticker/exchange lists were empty and the
  successor listed XOM/NYSE. Those mutable metadata observations do not by
  themselves prove historical continuity.

## Required TEK2day treatment

1. Preserve separate issuer/registrant and security identities. Record the dated
   successor relationship, security exchange terms, evidence and provenance.
   Ticker XOM is a dated listing attribute, not proof the security was unchanged.
2. Preserve each financial observation's original CIK, accession, reporting
   period and accounting basis. Query both relevant registrants under the
   reviewed relationship. Do not merely change one metadata CIK and abandon
   old filings, or relabel old observations as filings of the new registrant.
3. Compare overlapping periods (including the twice-filed June 10-Q) and retain
   both observations. Select one coherent reporting series for display/TTM;
   never count a duplicated quarter twice. Subsequent restatements remain
   separate observations requiring explicit selection.
4. Validate price continuity, share basis, quotes and estimate horizons
   separately. One-for-one common-share exchange does not authorize joining
   preferred shares, debt, ADRs or every series with the same ticker.
5. Build a bounded dry run and rollback before any live XOM repair or new SEC
   enrollment. Do not use the BK/BNY simple-rename executor for this event.

The pushed SEC fallback currently enrolls BNY only. It verifies a binding's CIK
and security identity and refuses mismatches. It does **not** implement automatic
multi-registrant succession; XOM is not enrolled. Existing XOM Firestore records
were not inspected or changed in answering this question. A separate reviewed
successor implementation is needed before claiming this case is handled.
