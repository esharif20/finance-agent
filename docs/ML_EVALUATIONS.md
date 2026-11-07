# Evaluations

Status: draft for review.

## Rules

1. Every intelligence ticket writes its evaluation first: dataset, metric, baseline to beat.
2. Synthetic evaluations run in CI and block merges on regression.
3. Real-data evaluations run only on the Mac; metrics are displayed, never committed.
4. Results are reproducible: fixed seeds, versioned datasets, model and prompt versions recorded. Synthetic results are committed to `evals/results/`.
5. New model versions run in shadow and are promoted only when they beat the live version.

## Financial correctness (property tests, Hypothesis)

| Invariant | Test |
| --- | --- |
| Re-importing a file changes nothing | import twice, Ledger identical |
| Overlapping exports never duplicate | random overlapping windows of one synthetic history |
| Ledger reconciles with bank balances | sum of Transactions = balance change, or a Reconciliation Gap is recorded |
| Money is exact | all amounts `Decimal`/minor units; no float anywhere in `ledger` (lint rule) |
| Transfers net to zero | linked Transfer pairs sum to zero across Accounts |
| Refunds count when received | ADR 0004 cases |
| Pending never counts | totals unchanged by pending rows |
| Allocation Plans are feasible | plan totals equal income; every hard constraint satisfied |

## ML quality

| Component | Metric | Baseline |
| --- | --- | --- |
| Categorisation | macro F1, per-Category precision and recall, expected calibration error | Corrections + rules only |
| Unknown-category detection | AUROC on held-out Categories | max-probability threshold |
| New-category proposals | precision of proposals the user accepts | none (track) |
| Merchant normalisation | exact-match accuracy on synthetic descriptors | lower-case + strip digits |
| Recurring detection | precision and recall on synthetic schedules | same amount ± 5%, same interval |
| Forecasting | MASE, prediction-interval coverage | seasonal naive |
| Preference prediction | log loss and calibration on accept/dismiss | global acceptance rate |
| Recommendation ranking | offline replay: acceptance of top 3 | rank by £ impact |
| Change-point detection | detection delay and false alarms on synthetic shifts | none |

### Categorisation (#112, #113)

`evals/categoriser.py` scores two models on a seeded synthetic dataset of Merchant descriptors (seed 112, dataset v2: 320 Correction rows over 160 corrected Merchants, at least 5 per Category, and 259 test rows of clean, noisy, unseen, lookalike and refund descriptors). It runs through `evals/gate.py`, so CI fails unless the committed results reproduce, and `--write` refuses to lower any metric.

- **Corrections + rules** (the baseline, ADR 0005). Its version is a fingerprint of the rule data (`rules-<sha>`), so a rule change shows as a new version.
- **Corrections + rules + nearest neighbour**: what they leave Uncategorised takes the Category of the most similar Correction-labelled Merchant, with cosine similarity as the confidence. In CI it embeds with `TrigramEmbedder` (hashed character trigrams); `--ollama` runs nomic-embed-text on the local Ollama into `categoriser_ollama.json`, which CI cannot reproduce and never checks.

Gated metrics, per model: macro F1, accuracy, coverage, ECE, ECE on assigned rows, and every Category's precision and recall, so one Category cannot collapse behind a steady macro F1. Plain ECE scores an abstention as confidence 0 and wrong, so a model that abstains a lot looks well calibrated; read `ece_assigned` beside `coverage`.

| Model | Macro F1 | Accuracy | Coverage | ECE | ECE (assigned) |
| --- | --- | --- | --- | --- | --- |
| Corrections + rules | 0.775 | 0.656 | 0.676 | 0.019 | 0.029 |
| + nearest neighbour, trigrams (CI) | 0.815 | 0.807 | 0.992 | 0.079 | 0.080 |
| + nearest neighbour, nomic-embed-text (local) | 0.838 | 0.830 | 0.992 | 0.081 | 0.082 |

Nearest neighbours fill noisy descriptors almost perfectly (noise accuracy 0.76 → 0.99) and help on unseen Merchants (0.25 → 0.50 with nomic), but raw similarity is a poor confidence: ECE quadruples. It does not beat the baseline on calibration, so it stays a suggester until #115 calibrates a threshold. Lookalikes stay at 0: a rule or a seeded alias decides them before any neighbour is asked.

### Recurring detection (#104)

`evals/recurring.py` scores `categorise.recurring.detect_recurring` on 400 seeded synthetic schedules (seed 104, dataset v1). Gold recurring: weekly, monthly, annual, monthly with ±3 days and ±3% jitter, skipped months and price rises. Gold not recurring: irregular dates, one-offs, monthly visits with varying amounts, and drifting amounts. The metric is binary precision and recall. The baseline is "same amount ± 5% of the first charge, same interval ± 1 day as the first gap". Results are committed to `evals/results/recurring_baseline.json`; `tests/test_eval_recurring.py` fails when the detector falls below its committed scores or below the baseline.

Detector v1: precision 1.0, recall 0.65. Baseline: precision 1.0, recall 0.12. Skipped months and price rises are missed by design (precision first). Known services and descriptor patterns cover them in recognition, and the user can add any entry.

### Intelligence Engine harnesses (#146)

`evals/synthetic_history.py` generates seeded 36-month histories for six fictional users, with payday cycles, seasonality (December, summer, winter energy) and planted events as ground truth: price rises, Category spikes against the seasonal normal, annual renewals and payday shortfalls. Each harness calls an interface the M5 tickets implement, with a stub until then, and reports per event type, per Category or per user, not only overall:

| Harness | Interface | Metric | Baselines |
| --- | --- | --- | --- |
| `evals/baselines.py` | `engine.baselines` (#147) | WAPE of the seasonal baseline against the true seasonal normal (overall, per Category, first year and after), and of the payday baseline on each cycle's first-week spend | trailing three-month mean |
| `evals/detectors.py` | `Detector` (#147 spikes, #148 the rest) | precision and recall per event type | trivial rules |
| `evals/ranking.py` | `Ranker` (#150) | NDCG@3 and @5 against the planted-relevance oracle | rank by £ impact |
| `evals/forecast.py` | `Forecaster` (#149) | MASE and 80% interval coverage gap, rolling origin from 13 months | naive, seasonal naive |

**Personal baselines (#147).** `seasonal_baseline` is the median of the last six months until a year exists, then the median of the same calendar month in up to three earlier years. `payday_baseline` is the median cycle's spend times the share spent by each day into the cycle, pooled over complete cycles. On the committed run, against the trailing mean:

| Metric (WAPE, lower is better) | Personal baseline | Trailing mean |
| --- | --- | --- |
| Seasonal, all Category-months | 0.102 | 0.190 |
| Seasonal, first year | 0.190 | 0.214 |
| Seasonal, after a year | 0.078 | 0.184 |
| Payday, first week of a cycle | 0.472 | 0.629 |

It loses on one Category, Coffee (0.079 against 0.057). That Category has no seasonality, so one earlier same month is noisier than three recent ones. Category spikes over 1.5× the seasonal baseline score precision 0.35 and recall 1.00, against the trivial rule's 0.07 and 0.78. All 34 false positives fall in a Category's first year, before the baseline can know the season.

`evals/gate.py` is the shared gate: CI fails unless the committed results equal a fresh run, and `--write` refuses to lower any committed metric, or to change the dataset without a version bump, unless `--accept-lower` is passed. Every model in a results file records its name and version.

### Forecasting (#149)

`engine.forecast.forecast_category` is ETS(A,N,A) on log(1 + monthly spend), with the initial states estimated, an error variance divided by its degrees of freedom (residuals less the 14 estimated parameters) and a Student's t interval. `cash_flow` adds recurring payments, projected from their schedules (`categorise.recurring`, minus registry entries the user marked not a subscription or cancelled), to the Category forecasts; Category bounds add as if fully correlated, so the total band is conservative. Recurring income (Income credits from one payer, scheduled from its last six so an old pay rise does not hide a salary) is projected with its dates for the what-if (#151); one-off credits are not.

The backtest (`evals/forecast.py`, seed 146, dataset v2, origins from 13 months, horizon 3) first ran ETS from 13 months with the first year as its initial seasonal states. It lost to seasonal naive on 13 to 23 months of training on all three seeds tried (146, 149, 7) and won from 24, so the ETS gate is tuned to 24 months and seasonal naive serves 12 to 23. Committed results (MASE, then 80% interval coverage):

| Category | ETS | Seasonal naive |
| --- | --- | --- |
| Coffee | 0.927, 0.803 | 1.035, 0.712 |
| Eating out | 0.648, 0.808 | 0.688, 0.682 |
| Groceries | 1.090, 0.788 | 1.047, 0.778 |
| Takeaway | 0.818, 0.768 | 0.919, 0.775 |
| Online | 0.746, 0.828 | 0.789, 0.684 |
| Public transport | 0.592, 0.833 | 0.623, 0.758 |
| Holidays | 0.314, 0.876 | 0.368, 0.722 |
| Overall | 0.734, 0.815 | 0.781, 0.730 |

ETS beats seasonal naive on overall MASE and coverage gap and on MASE in six of seven Categories; Groceries is the exception. On the untuned seeds 149 and 7, overall MASE is 0.803 against 0.823 and 0.785 against 0.814. The engine's seasonal-naive band pools month-to-month and same-month errors: from same-month errors alone (one at 13 months) it covered about 68% at 12 to 23 months, so the verifier of #179 widened it, and overall coverage moved from 0.741 to 0.815. Holidays, the lumpiest Category, now over-covers (0.876). Groceries loses on MASE on seed 146 but not on every seed: across seven seeds the ETS losses from 24 months are scattered (Groceries on 3, Online on 4, Coffee on 3) while ETS wins overall on every seed, so no Category falls back to seasonal naive. A constant training window has no MASE scale, so the harness scores that origin for coverage only (verifier #171).

## Minimum-data gates (ADR 0015)

A learned component runs live only when the user's own data clears its gate. Below the gate, the baseline or rules decide, and the "How well I know you" page says which gate is not yet met. Gates are starting values: each ticket confirms or tunes its gate in its evaluation.

| Component | Gate | Fallback |
| --- | --- | --- |
| Categoriser: embedding nearest neighbours (first M3 candidate) | ≥ 200 Corrections and ≥ 5 per Category it may assign | Corrections + rules |
| Categoriser: calibrated classifier on embeddings + amount/day features | Beats the embedding nearest-neighbour model on macro F1 and calibration | Embedding nearest neighbours |
| Recurring detection | ≥ 3 occurrences of the series | Not flagged |
| Personal baselines and anomaly scores | ≥ 6 months for the Category, counted from its first Transaction (seasonal); ≥ 6 complete pay cycles ending after that first Transaction (payday). Both count spending after Refunds. Confirmed by #147: the seasonal baseline beats the trailing mean from month six | No anomaly Insight; the baseline returns `Insufficient` |
| Forecasting (ETS) | ≥ 24 months of history (tuned from 13 by #149's backtest) | Seasonal naive at ≥ 12 months; "insufficient" before |
| Change-point detection | ≥ 9 months for the series | None |
| Preference prediction / contextual bandit | Starts from priors on day one, and needs no gate | Rank by £ impact |

Embeddings come from a pretrained local model (Ollama), used for inference only (ADR 0009). The model name and version are recorded with every evaluation result.

## Learning from interaction

- **Active learning in the Review Queue (M3).** The queue orders Transactions by expected information gain, using classifier uncertainty weighted by how often the Merchant appears, so each Correction teaches the most. Metric: macro F1 reached per Correction, against a random-order and a newest-first queue.
- **Contextual bandit for Insights and their timing (M5–M6).** Thompson sampling with a Beta, or a Bayesian linear model on context: time of month, recent spend and Category.
  - **Reward:** accepted and acted on, plus regret-review outcomes.
  - **Never** opens, clicks or time on screen.
  - **Exposure cap:** a fixed maximum number of Insights and alerts per week, whatever the bandit prefers.
- **Propensity logging from the first Insight (M5).** Every shown item records the policy version and the probability it had of being shown. Candidate policies are compared by off-policy evaluation (inverse propensity scoring and self-normalised IPS) on these logs before any shadow run.
- **Out of scope:**
  - full reinforcement learning (ADR 0015);
  - training deep models from scratch on real data;
  - fine-tuning on real data (ADR 0009).

## Assistant quality

Golden set of ~100 synthetic questions with expected intent, tool calls and figures; it doubles as the model benchmark.

| Metric | Target |
| --- | --- |
| Tool selection accuracy | high; compared across models |
| Argument validity | high |
| Grounding pass rate | 100% |
| Correct refusals | high |
| Injection suite pass rate | high (malicious descriptors, CSV cells, chat) |
| Memory correctness | Proposals only when supported by the conversation |
| Explanation quality | local LLM judge with fixed rubric; cloud judge on synthetic data; human spot-check |
| Latency and peak memory on M1 Pro 16 GB | recorded per model |

## Outcomes over time

The "How well I know you" page tracks: categorisation accuracy on recent Corrections, forecast error, preference calibration, Insight acceptance rate, outcomes of accepted recommendations, and monthly cloud spend.
