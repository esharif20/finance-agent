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

## Minimum-data gates (ADR 0015)

A learned component runs live only when the user's own data clears its gate. Below the gate, the baseline or rules decide, and the "How well I know you" page says which gate is not yet met. Gates are starting values: each ticket confirms or tunes its gate in its evaluation.

| Component | Gate | Fallback |
| --- | --- | --- |
| Categoriser: embedding nearest neighbours (first M3 candidate) | ≥ 200 Corrections and ≥ 5 per Category it may assign | Corrections + rules |
| Categoriser: calibrated classifier on embeddings + amount/day features | Beats the embedding nearest-neighbour model on macro F1 and calibration | Embedding nearest neighbours |
| Recurring detection | ≥ 3 occurrences of the series | Not flagged |
| Personal baselines and anomaly scores | ≥ 6 months for the Category, counted from its first Transaction (seasonal); ≥ 6 complete pay cycles (payday). Confirmed by #147: the seasonal baseline beats the trailing mean from month six | No anomaly Insight; the baseline returns `Insufficient` |
| Forecasting (ETS) | ≥ 13 months of history | Seasonal naive at ≥ 12 months; none before |
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
