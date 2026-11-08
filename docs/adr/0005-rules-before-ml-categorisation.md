---
area: backend
status: accepted
---
# Categorisation runs Corrections, then rules, then ML; ML must earn its place

A Transaction's Category comes from the first confident source in this order: the user's Corrections for its Merchant, deterministic rules, then a local ML classifier. Anything below the confidence threshold goes to the Review Queue instead of being guessed, and every assignment records its source. The ML classifier ships only if it beats Corrections plus rules alone on a fixed synthetic evaluation set (macro F1 and calibration). A reader might expect ML first; we chose this order because a few dozen repeat Merchants cover most spending, and rules are explainable and testable.

## Amendment (#215 B): confident guesses auto-apply right after sync

The user decided on 8 Nov 2026 (#215) that confident model guesses apply right after a sync. This replaces #115's gate of 200 Corrections and its shadow mode. The order stays the same: Corrections, then rules and the UK merchant base, then the embedding model, which only fills rows the rules left Uncategorised.

**The only gate is a calibrated threshold.** The model's guess for a Category Review item applies when its score clears that threshold. The threshold is the lowest similarity at which every band from there up keeps at least 95% precision on the synthetic #112 evaluation. It is set per model (`categorise/auto.py`, `THRESHOLDS`) and copied from the committed synthetic results (the `calibration` block). A model with no calibrated threshold never applies. Real-data metrics are never committed.

**A live accuracy monitor replaces shadow mode** (ADR 0015). Every guess is logged per model version, which is the model name plus its digest (`stored_model`). When the user answers a guess, or overrides an auto row, with a Merchant-wide Correction, the latest answer is recorded against it. The monitor only measures, except in one case. Once a version has at least 50 answers on guesses at or above its threshold and fewer than 95% of them agree, the version stops auto-applying for good, and its earlier auto rows go back to the Review Queue, so its old guesses never reach new rows. A re-pulled model is a new version with its own record.

Applied rows record the source `auto:<model version>`. A row below the threshold stays Uncategorised and goes to the Review Queue. A pass that applies anything bumps the ledger version, so caches keyed on it (ADR 0010) go stale. A pass joins the latest batch when nothing else has changed the ledger since that batch's own bump, so one sync (the import pass and the pass after new vectors) is one batch. Undo (`POST /api/review-queue/undo-auto`) takes back the latest batch in one action, and those Merchants never auto-apply again. Corrections remain an overlay on read (ADR 0001), so they always win over an `auto` row. Auto rows are never used as labels. The log (`category_prediction`, `model_suspension`) holds ids, Category ids, scores and the version only, never descriptions or amounts.

## Amendment (#215 F): every Merchant gets a guess; a local LLM and an agreement gate decide how sure

The user decided on 8 Nov 2026 (#215) that the app should file almost everything itself, down to the sub-Category, and ask about only a handful of Merchants. The order is unchanged: Corrections, then rules and the UK base, then the models, which only see Merchants the rules leave Uncategorised. Classification is per Merchant, never per row.

**A local LLM joins the embedding kNN** (`categorise/llm.py`). It runs once per new Merchant after a sync, in a background thread, on the local Ollama chat model with the Assistant's settings (think off, temperature 0). Its reply is JSON constrained to the taxonomy's sub-Category ids or "unsure", so it can only name a Category. It sees the payee, a few description tokens, amount patterns in words (median band, cadence, weekday spread; never an amount) and the 5 nearest labelled Merchants as few-shot examples: the user's Corrections and the UK base Merchants, found by the embedder. Nothing is fine-tuned (ADR 0015). The answer is cached in `category_prediction` per Merchant and model version (`llm:<name>@<digest>`), so no migration was needed. Descriptions go to localhost only and are never logged.

**Two tiers.**

- Confident, source `auto`: Corrections, rules and the UK base only. No model guess is confident (#287). Held out, with cross-validation over Merchants in `evals/categoriser_gate.py`, no setting (kNN alone, kNN and LLM agreeing, or the LLM alone) kept 95% precision on enough unseen Merchants. `auto.THRESHOLDS` is empty. A version added there would apply as `auto:<version>` again, and it would first need its own held-out calibration.
- Low, source `auto-low:<version>`: every Merchant with any model signal gets its best guess (the LLM's on a disagreement, the kNN's when the LLM is unsure). Its held-out accuracy (0.73 on the synthetic set) is reported as it is, not gated: it is what the user sees without reviewing.

**Review asks only what is worth asking.** After the LLM pass, the `REVIEW_CAP` (10) Merchants with the most expected impact, spend × (1 − confidence), stay Uncategorised in the Review Queue with their suggestions. Everything else is filed, and Merchants with no guess take their places inside the cap first. The confidence of a low guess is its kind's accuracy on the synthetic set (`LOW_CONFIDENCE`). The import pass itself files only rules and the base; the low tier waits for the LLM's answers.

**Learning without asking.** An override of an `auto` or `auto-low` row is a Merchant-wide Correction. It wins at once (the overlay), is recorded against the guess by the live monitor, and becomes a labelled few-shot example and kNN neighbour for the next Merchants. A suspended version stays suspended whether or not it has a threshold. A suspended version files nothing in either tier, and neither does a gate version whose embedder or LLM is suspended. Its rows go back to Review at once. Each low-tier version has its own record: once it has filed 50 Merchants and fewer than 50% stand, it stops. Bank text reaches the LLM sanitised and quoted as `{"data": ...}`, through the sanitiser the Assistant uses (`finance_agent.sanitise`), so no description can forge a few-shot line. Undo takes back the latest batch, both tiers, in one action. Auto rows are still never labels.
