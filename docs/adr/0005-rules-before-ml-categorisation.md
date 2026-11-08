---
area: backend
status: accepted
---
# Categorisation runs Corrections, then rules, then ML; ML must earn its place

A Transaction's Category comes from the first confident source in this order: the user's Corrections for its Merchant, deterministic rules, then a local ML classifier. Anything below the confidence threshold goes to the Review Queue instead of being guessed, and every assignment records its source. The ML classifier ships only if it beats Corrections plus rules alone on a fixed synthetic evaluation set (macro F1 and calibration). A reader might expect ML first; we chose this order because a few dozen repeat Merchants cover most spending, and rules are explainable and testable.

## Amendment (#215 B): confident guesses auto-apply right after sync

The user decided on 8 Nov 2026 (#215) that confident model guesses apply right after a sync. This replaces #115's gate of 200 Corrections and its shadow mode. The order stays the same: Corrections, then rules and the UK merchant base, then the embedding model, which only fills rows the rules left Uncategorised.

**The only gate is a calibrated threshold.** The model's guess for a Category Review item applies when its score clears that threshold. The threshold is the lowest similarity at which every band from there up keeps at least 95% precision on the synthetic #112 evaluation. It is set per model (`categorise/auto.py`, `THRESHOLDS`) and copied from the committed synthetic results (the `calibration` block). A model with no calibrated threshold never applies. Real-data metrics are never committed.

**A live accuracy monitor replaces shadow mode** (ADR 0015). Every guess is logged per model version, which is the model name plus its digest (`stored_model`). When the user answers a guess, or overrides an auto row, with a Merchant-wide Correction, the latest answer is recorded against it. The monitor only measures, except in one case. Once a version has at least 50 answers on guesses at or above its threshold and fewer than 95% of them agree, the version stops auto-applying for good. Its earlier rows stay and can still be undone. A re-pulled model is a new version with its own record.

Applied rows record the source `auto:<model version>`. A row below the threshold stays Uncategorised and goes to the Review Queue. Each apply pass is one batch, keyed by the ledger version it ran at, so one sync is one batch. Undo (`POST /api/review-queue/undo-auto`) takes back the latest batch in one action, and those Merchants never auto-apply again. Corrections remain an overlay on read (ADR 0001), so they always win over an `auto` row. Auto rows are never used as labels. The log (`category_prediction`, `model_suspension`) holds ids, Category ids, scores and the version only, never descriptions or amounts.
