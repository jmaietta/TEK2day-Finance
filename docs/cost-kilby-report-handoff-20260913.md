# COST Deep Research delivery failure: next chatllm session

User prompt: `COST - research the performance of Costco's wine business as U.S.
wine sales remain flat to down.`

Reported Kilby error: `A financial amount is missing its fact, source-excerpt or
scenario reference.` No paid reproduction or live partner request was run in
this TEK2day session. Chatllm was not modified.

## Verified TEK2day observations

Bounded first-party reads September 13 returned COST / Costco Wholesale
Corporation / CIK 909832 / USD. Income, balance sheet and cash flow views loaded
successfully. This was a content/coverage check, not just an HTTP-status check:

- Quarterly income: all 13 displayed rows populated for provider-labeled
  August 2025, November 2025, February 2026 and May 2026 periods.
- Quarterly cash flow: all eight displayed rows populated for those periods.
- Annual income/cash flow: displayed rows populated for 2022-2025.
- Balance sheet: all nine displayed rows populated for the latest May 2026
  column. The older provider-labeled **2024-11-30 column has all nine displayed
  rows empty**. Other displayed balance-sheet columns are populated.

These are provider period labels, not an independent SEC fiscal-calendar audit.
No claim was made that all stored or source fields are complete, that the May
period is the latest legally published report, or that the values have been
independently reconciled with SEC filings. Zero remains a populated observation.
The displayed tables contain consolidated company fields; they do not establish
wine-business revenue, margins or market share.

No first-party TEK2day error corresponding to the reported Kilby validation
message was reproduced. The older balance-sheet gap is real, but there is no
evidence tying it to this failed research run. Its repair needs separate source
and period review; no COST database writes or enrollment occurred.

Private evidence is
`C:/tmp/tek2day-issue3-20260909/cost-firstparty-diagnosis-20260913.json`,
canonical SHA-256
`a5489d0ad854523929cc630f95fa79a1fdee737d4c354ba6d88459dd557f4318`.
Do not copy the raw private response bundle into a public fixture or commit.

## Self-contained prompt for the next chatllm session

Investigate the COST Deep Research failure quoted above in jmaietta/chatllm.
GitHub is the source of truth. Read AGENTS.md and current repository guidance;
preserve all uncommitted work. Verify the actual deployed Kilby build and failed
run before attributing its behavior to a source revision. GitHub main observed
in this session was `129c730b4afcc3156223de2dff9c84d7a313f63d`; the local chatllm
checkout reported a different HEAD, `58991c71bab75c43dd940b1ba6ce0d6f3058991f`.
The exact reported error literal was not located in the small subset of
published evidence-validation files inspected here; its producing path and
offending report unit still require investigation.

The user explicitly requires a useful supported Deep Research report even
when one datum is unavailable. A missing fact/source-excerpt/scenario reference
must not silently discard the entire completed report. Preserve source and
financial-ownership safeguards: attach an existing valid reference when the
evidence actually supports the amount; otherwise omit the unsupported amount
or section and explain the limitation. Do not fabricate a reference, imply a
wine segment disclosure exists, or weaken identity/amount checks to pass output.
Use bounded recovery and deliver the remaining supported report. If no usable
report can be delivered, reverse the credit charge idempotently; the user must
not spend $6 in credits and receive nothing. Check the actual ledger and final
delivery before any billing adjustment. Use saved fixtures/offline tests first;
obtain approval for paid research reruns or live account mutations.

TEK2day serving commit for the completed BNY price repair is
`4789708d72fa68af6aac78d68322951b6ef578d0`, API revision
`tek2day-api-00139-rsq`. See `price-history-repair.md` and the execution record
for the exact BNY results. Existing financial repair behavior is preserved.
COST requested/resolved/payload remains COST. No new redirect or
`security_resolution` contract was enabled: BNY is exact-symbol for the
partner API and BK remains a strict refusal there. Use Kilby's existing
authorized partner path for live evidence; never impersonate Kilby from a local
TEK2day client. No permitted frozen raw partner fixture was captured here.

Keep chatllm #221 open and #233 closed. TEK2day #3 still needs the next normal
scheduled financial-run acceptance and its remaining documented coverage work.
After the current repairs and this urgent report-delivery bug, the user wants
CEORater re-enabled in TEK2day Finance and Kilby using the repaired CEORater
repository/dataset/API/MCP. Verify those current contracts and existing grants
before activation; CEORater has not been re-enabled by this session.
