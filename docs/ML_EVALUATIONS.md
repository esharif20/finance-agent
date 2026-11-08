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

`evals/recurring.py` scores `categorise.recurring.detect_recurring` on 440 seeded synthetic schedules (seed 104, dataset v2). Gold recurring: weekly, monthly, annual, monthly with ±3 days and ±3% jitter, skipped months and price rises. Gold not recurring: irregular dates, one-offs, monthly visits with varying amounts, drifting amounts and fixed-price café visits. The metric is binary precision and recall. The baseline is "same amount ± 5% of the first charge, same interval ± 1 day as the first gap". Results are committed to `evals/results/recurring_baseline.json`; `tests/test_eval_recurring.py` fails when the detector falls below its committed scores or below the baseline.

Detector v1 (whole history): precision 1.0, recall 0.65. Baseline: precision 1.0, recall 0.12.

Detector v2 (#177) judges the latest run of charges. A history regular as a whole is one run, which keeps every v1 hit. Otherwise the run grows back from the latest charge while each earlier charge is one cadence window earlier and within ±5% of the run's median so far, and it is flagged at 3 or more charges. An old skipped month, price rise or one-off no longer blocks a current series, and a price step starts a new run (judging the step is #148's job). Results: precision 1.0, recall 0.86. Flagged rates: skipped months 0 to 0.63, price rises 0 to 0.65, ±3% jitter 0.875 to 0.9, drift stays at 0. The remaining misses are series whose latest run is shorter than 3 charges (a skip or rise in the last two periods). Known services and descriptor patterns cover these in recognition, and the user can add any entry. Three charges cannot tell ±3% jitter from slow drift, so the step rule compares each earlier charge with the run's median before that charge joins. Banding against the true median instead let drift through (precision 0.87).

Dataset v2 (verifier #183) adds 40 fixed-price café histories (6 to 20 visits at one price, 3 to 12 days apart), a negative the random-amount cases never tested. Judging only the latest run flagged 25% of them, because the last three visits are often a week apart (v1: 0%). So every earlier charge at the run's price must be a whole number of periods from the next (k times the window's centre, give or take half the window): one price bought more often than the cadence, or off it, is not a series. Results on v2: precision 1.0, recall 0.86, café histories flagged 0. Short histories of 4 visits that look weekly as a whole stay ambiguous to any cadence rule (v1 flags them too), so the case starts at 6.

### Intelligence Engine harnesses (#146)

`evals/synthetic_history.py` generates seeded 36-month histories for six fictional users, with payday cycles, seasonality (December, summer, winter energy) and planted events as ground truth: price rises, Category spikes against the seasonal normal, annual renewals and payday shortfalls. Each harness calls an interface the M5 tickets implement, with a stub until then, and reports per event type, per Category or per user, not only overall:

| Harness | Interface | Metric | Baselines |
| --- | --- | --- | --- |
| `evals/baselines.py` | `engine.baselines` (#147) | WAPE of the seasonal baseline against the true seasonal normal (overall, per Category, first year and after), and of the payday baseline on each cycle's first-week spend | trailing three-month mean |
| `evals/detectors.py` | `engine.detectors` (#147 spikes, #148 price rises and renewals, #151 shortfall) | precision and recall per event type; each hit's evidence and impact | trivial rules |
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

**Detectors (#148).** `engine.detectors` turns rows into typed Insight candidates. Each one carries an impact in integer pence computed by code, a confidence in percent (fixed per rule until #150 calibrates it) and the Transaction ids behind it. The spike rule moved here from the harness. A price increase is a charge more than 5% above the `categorise.recurring` series it follows, one cadence after that series' last charge. Since #177 a price step starts a new run, so the step is judged between runs, and its impact is the rise over a year. An upcoming renewal is raised 28 days before a charge's anniversary, unless the Merchant charges again first. It comes either from an annual series (any Category, which needs 3 charges by the ADR 0015 gate) or, below that gate, from a rule: a Subscriptions charge with nothing from the Merchant in the 300 days before it. Its impact is the fee last paid. A Merchant the user cancelled or marked not a subscription (`stopped`, verifier #188) gets neither a price increase nor a renewal; its rows still count towards spikes. Recurring payments and spikes return `Insufficient` below their gates, and so do price increases when no Merchant has 3 charges yet. The harness now also scores each true positive on what it says. `evidence_valid` checks that the evidence is non-empty and lies within the planted event's rows. `impact_consistent` checks a price rise or fee to within 5%, and a spike's excess so far to be positive and at most 1.5× the planted excess. Committed run (candidate v2 against the trivial rules):

| Type | Precision | Recall | Evidence, impact |
| --- | --- | --- | --- |
| Price rise | 1.00 (0.01) | 1.00 (1.00) | 1.00, 1.00 |
| Category spike | 0.35 (0.07) | 1.00 (0.78) | 1.00, 1.00 |
| Upcoming renewal | 1.00 (0.40) | 1.00 (1.00) | 1.00, 1.00 |
| Payday shortfall | not run (0.04) | 0 (1.00) | #151 |

The Subscriptions rule is what lets a renewal be predicted from one or two charges. It depends on categorisation: an annual fee filed elsewhere is not flagged until it forms a series. Spike precision is unchanged from #147, and the threshold was not tuned on this set, which has only 18 spikes.

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

### Model benchmark (#143)

`evals/assistant_bench.py` runs the whole graph on the golden set in `evals/assistant_golden.py`. Each case is a question, its route, the expected tool calls in order, and a grounded answer. v1 has 20 cases: Ask, Memory and Correct, plus five refusals (a payment, an unconfirmed bulk write, a figure as a Memory, a plan before M5, and investment advice). They run over the synthetic Ledger of `tests/synthetic_assistant.py`. Each case runs in a rolled-back transaction on a throwaway encrypted database, keyed in memory.

| Metric | Passes when |
| --- | --- |
| `route_accuracy` | the Router picks the case's sub-graph |
| `tool_call_accuracy` | the model's structured calls match the expected ones in order. Arguments are a subset match: dicts key by key, lists item by item, and an expected string must appear in the model's, ignoring case |
| `grounding_pass_rate` (non-refusals) | the reply came from the model, passed the grounding check (`path == local_model`), and contains each expected figure |
| `refusal_correctness` (refusals) | nothing was proposed: no `propose_*` call and no pending Proposal |
| `printed_tool_call_rate` (lower is better) | the qwen tool-call printing bug: a reply whose text holds a tool call (a `<tool_call>` or `<function=` marker, a `{"name": <tool>, "arguments"/"parameters": ...}` object, or `<tool>(`) |
| `model_error_rate` (lower is better) | Ollama errors, including malformed tool calls it refuses |
| `latency_p50_ms`, `latency_p95_ms` | nearest-rank percentiles of the wall time of each turn (real runs only) |
| `peak_rss_gb`, `peak_ollama_size_gb`, `peak_memory_gb` | peak RSS of Ollama's runner process and peak `/api/ps` size, sampled every 0.25 s, and the larger of the two (real runs only) |

- **CI** runs it on two scripted `FakeChatModel`s and requires `evals/results/assistant_fake.json` to reproduce (`gate.py`). The oracle does what each case expects and scores 1.0 everywhere. The negative control prints its tool calls as text, and must be flagged on every tool-using case: it scores 0.25 tool-call accuracy and a 0.75 printing rate.
- **The local run is opt-in:** `uv run python evals/assistant_bench.py --model qwen3.5:4b`.
  - It talks only to localhost Ollama, at `num_ctx` 8192.
  - It merges the model's scores into `evals/results/assistant_local.json`, which CI never reads. The model digest is its `model_version`.
  - It unloads the model afterwards (`keep_alive` 0), even if the run fails.
  - Run it on an idle Mac.
- **Choosing the model (#144):** the smallest model that passes the accuracy gates with `peak_memory_gb` of about 7 or less.
- **Known gap:** a printed tool call with no figures in it passes the grounding check, so the user sees it as the reply. The printing metric catches it here.

## Outcomes over time

The "How well I know you" page tracks: categorisation accuracy on recent Corrections, forecast error, preference calibration, Insight acceptance rate, outcomes of accepted recommendations, and monthly cloud spend.
