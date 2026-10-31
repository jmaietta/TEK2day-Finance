# TEK2day Finance: Claude Instructions

## Nothing is built without talking to Kilby first
Owner, 2026-10-31: "TEK2day cant build anything without talking to us first."

This covers every change: features, fixes, scheduled jobs, settings, data migrations, and API fields. Size doesn't matter.

1. **Design first, no code.** Send the Kilby Claude session (find it with ListAgents) a design that says:
   - what changes, and why;
   - where each figure is computed (stored ahead of time, or on request);
   - the cost: reads, writes, and outside requests such as Yahoo or SEC;
   - the latency at the largest batch any caller sends;
   - every caller of what changes: Kilby's (ask the Kilby session for its current list, with timeouts, batch sizes, and whether a partial answer is accepted), the website, and the terminal;
   - how it fails, and what each caller sees when it does.
2. **Agree it together.** The Kilby session checks the design against Kilby. Settle every disagreement between the two sessions. Don't send the owner technical back-and-forth.
3. **One design to the owner.** Give him one agreed design with its size in hours, its running cost, and anything he must approve, such as a new scheduled job. Recommend one course; don't offer a menu.
4. **His go, then code.** No code, branch, or PR before he says go. Changing course mid-build counts as a new design: back to step 1.

Why: TEK2day's beta (PR #26/#28) went live computing every beta on request, about 107 reads per symbol, and nobody checked that against Kilby's "Analyze my watchlist" (250 symbols, a 30 s timeout, no partial answer). PR #29 was then built on the same design and had to be replaced. That was three PRs for one feature. His words: "you and the tek2day agent need to get your shit togther. I have told you before."

## Changing what Kilby reads
Tell the Kilby session before changing or removing any field, endpoint, or behavior that Kilby reads, and wait for its answer. Adding a field is safe only once the Kilby session confirms it.

## Pushing and merging
- `main` takes pull requests only, and they need a green pytest. The owner merges. Never use an admin bypass.
- Ask the owner before every push. One yes covers one push.

## Keys and secrets
Never read, print, pass, or save an API key, token, or secret, and never put one in a process's environment by hand.

## Architecture
`architecture.yaml` is checked against Google Cloud and this repo by `architecture_check.py`. When something changes on purpose, update `architecture.yaml` in the same commit.
