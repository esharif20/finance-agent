# AI design

Status: draft for review. Decisions: ADRs 0005, 0007–0013.

## Principle

The LLM is never the source of financial truth. The Intelligence Engine computes; the Assistant explains (ADR 0007).

## The Assistant

One LangGraph graph (ADR 0008):

```mermaid
flowchart LR
  Q[Message] --> C{Cache hit?}
  C -- yes --> R[Reply]
  C -- no --> RT[Router]
  RT --> A[Ask]
  RT --> P[Plan]
  RT --> M[Memory]
  RT --> K[Correct]
  A & P & M & K --> G[Grounding check]
  G --> H{Proposal?}
  H -- yes --> U[Confirm card]
  H -- no --> R
  U --> R
```

| Sub-graph | Does | Tools |
| --- | --- | --- |
| Ask | Answers questions, explains Insights | `spending_summary`, `category_trend`, `list_insights`, `explain_insight`, `list_subscriptions`, `goal_status` |
| Plan | Allocation Plans, what-if simulations, trade-offs | `allocation_plan`, `simulate_purchase`, `goal_status`, `forecast_cashflow` |
| Memory | Elicits preferences, proposes Memories, re-checks conflicts | `list_memories`, `propose_memory` |
| Correct | Proposes Corrections (recategorise, mark transfer) | `find_transactions`, `propose_correction` |

Rules:

- Tools are read-only, Pydantic-typed and allowlisted per sub-graph. The only writes are Proposals, which do nothing until the user confirms.
- Tool results are aggregates, at most 20 rows, each with an evidence ID. Descriptions are marked as data, truncated to 60 characters, instruction-like text stripped.
- **Grounding check**: every £ figure, percentage and date in a reply must match a value from a tool result in that turn. Fails → regenerate once → deterministic fallback. Conventions (`assistant/grounding.py`, #137):
  - **Sign is exact.** "£40" is +£40 and matches only a positive value; "-£40" matches only a negative one. Tools report aggregates (money out, spending, Category totals, trends) as positive figures, so "you spent £40" fits them. A Transaction row is signed as the Bank has it (spending negative), so the graph (#139), when it turns a Spending row into `ToolValue`s, also gives its positive outflow. A signed figure that can go either way (net, a balance) is never given unsigned, so "you saved £40" can't pass on a net of -£40.
  - **Periods in words count as dates.** "March" must match the one tool month or date in March. "Last month" and "this month" are read against today; "last Tuesday" and a bare "Tuesday" must name a tool date within the six days either side. "3 March" with no year grounds only when exactly one tool date has that day and month.
  - **Every other number fails closed.** A whole number is a count and must equal a tool count, including digits in a Merchant name or a year standing alone. Figures in words, superscripts, fractions and other non-digit numerals never ground. Digits from other scripts (full-width, Arabic-Indic) are read as their value.
- Graph checkpoints live in the encrypted database. No LangSmith, no external tracing; CI egress test.
- **The graph (#139, `assistant/graph.py`).** The Router is one model call answering one word; anything else goes to Ask, the read-only sub-graph. Each sub-graph offers only its allowlisted tools, for at most 3 rounds of up to 4 calls; a refused call (another sub-graph's tool, bad arguments, another user's Transaction) comes back to the model as a fixed error, never its text. Tool results become `ToolValue`s by `assistant/values.py`: money signed as the tool gives it, a Spending Transaction row also as its positive outflow, `YYYY-MM` as a month, ISO dates as dates, whole numbers as counts; ids, flags and `{"data": ...}` text are never figures. The regeneration is offered no tools. A Proposal pauses the graph (`interrupt`) on a confirm card; the chat API resumes it with the user's decision, or the card is answered at `/api/proposals/{id}`. The checkpointed state holds the message, reply and route, never tool results.
- **Streaming.** The chat API streams a reply (server-sent events) only after the grounding check, so no unchecked figure reaches the screen; the `meta` event says which path answered (`local_model`, `deterministic`, later `cache`).

## Models

`ChatModel` interface with adapters: Ollama now, cloud later through the gateway. Model choice is benchmark-driven (see `ML_EVALUATIONS.md`). Note: community reports describe a qwen3.5 tool-call printing bug in some Ollama versions; the benchmark must catch it.

The default is **`qwen3.5:9b-q4_K_M`** (#144). `OllamaChatModel` sends `think: false`, `temperature` 0, `presence_penalty` 0 and `num_ctx` 8192 on every call, whatever the model's own defaults (qwen3.5 ships temperature 1 and presence_penalty 1.5).

Benchmark on the M1 Pro 16 GB, Ollama 0.40.15, golden set v2 (25 cases, 6 refusals), 8k context, greedy, final prompts (`evals/results/assistant_local.json`):

| Model | route | tools | grounding | refusal | printed | errors | p50 / p95 | peak |
|---|---|---|---|---|---|---|---|---|
| `qwen3.5:4b-q4_K_M` | 0.96 | 0.80 | 0.74 | 0.83 | 0 | 0 | 7.5 s / 21.1 s | 4.7 GB |
| **`qwen3.5:9b-q4_K_M`** | 0.96 | 0.88 | 0.74 | 1.00 | 0 | 0 | 16.5 s / 36.1 s | 6.7 GB |
| `gemma4:e4b-it-q4_K_M` | 0.96 | 0.76 | 0.42 | 0.83 | 0 | 0 | 4.5 s / 10.2 s | 6.5 GB |
| `lfm2.5:8b-a1b-q4_K_M` | 0.44 | 0.48 | 0.00 | 1.00 | 0.08 | 0 | 23.3 s / 38.8 s | 5.1 GB |
| `qwen3.5:4b` (nvfp4, MLX) | 0.96 | 0.76 | 0.68 | 1.00 | 0 | 0 | 5.3 s / 14.7 s | 11.8 GB |
| `gemma4:e4b` (nvfp4, MLX) | 0.96 | 0.76 | 0.37 | 0.83 | 0 | 0 | 2.8 s / 10.7 s | 11.4 GB |

Before the #144 fixes (thinking off, default sampling, old prompts), `qwen3.5:4b` scored route 0.56, tools 0.48, grounding 0.47, and `qwen3.5:9b` route 0.96, tools 0.48, grounding 0.53 at 15.2 GB.

What the benchmark found:

- **Memory.** The default `qwen3.5` and `gemma4` tags are nvfp4 builds on Ollama's MLX runner. Its memory grows over a session (its log shows a 4–5 GB peak after one question and about 12 GB after 25), so they fail the about-7 GB budget. The GGUF `q4_K_M` tags on the llama.cpp runner stay flat.
- **Fixed in the prompts and tools.** Tool argument descriptions were code comments the model never saw, so it sent £50.00 as `amount_minor` 50 and guessed Category names. Each argument now has a schema description, `propose_correction` lists the taxonomy, the system prompt states today's date (models invented 2023 dates for `on`), `simulate_purchase` echoes its day, and the Router prompt has examples.
- **Model errors left (grounding stays strict).** Both qwen sizes still write `£0` for £0.00, work out a Split's remainder (£9.99), or add a note to a Category change. Those replies fall back to the deterministic table. `qwen3.5:4b-q4_K_M` proposes "I spend about £92.34 a month on food" as a Memory, and the 9b is the smallest model that refuses all six unsafe asks.
- **The golden set is ambiguous on transfers:** "Send £50.00" expects Ask and "Move £500.00" expects Plan, so both models lose one route either way.

Routing per request: cache → local model → cloud only with per-conversation opt-in → deterministic tables. The UI always shows which path answered.

## Context engineering

Each turn's context is assembled, never accumulated:

| Slot | Contents | Budget |
| --- | --- | --- |
| System | Role, rules, tool schemas for the routed sub-graph only | fixed |
| Memories | Relevant confirmed Memories, selected by type and tag in SQL | ≤ 800 tokens |
| Conversation | Last few turns of this session | ≤ 1,500 tokens |
| Session summary | Short summary of recent sessions | ≤ 300 tokens |
| Tool results | This turn's results | ≤ 2,500 tokens |
| Playbook | Cards whose Engine conditions hold, already filled from Engine figures and dated facts (#254) | ≤ 600 tokens |

Financial facts never enter context except as this turn's tool results: digits in earlier replies are masked in the conversation slot. Memories enter as quoted `{"data": ...}`, sanitised like bank text. Tokens are counted as 4 characters until the benchmark (#143) needs the model's tokenizer.

## Memory

Five stores: financial facts (never memorised, always recomputed), Memories (confirmed preferences, goals, constraints), conversation (session only), session summaries (90 days), feedback (kept).

A Memory exists only through a confirmed Proposal. It carries provenance (turn, date, the user's words), a type and a review date. Patterns in spending become Insights, never Memories. When behaviour contradicts a Memory, the Assistant asks.

## Answer cache

Keyed by (intent, tool calls with arguments, Ledger version), stored encrypted. Embedding similarity only maps wording to intent, never returns a cached answer (ADR 0010).

As built (#140, `ledger/answer_cache.py`): the key is a SHA-256 of the canonical JSON of (intent, tool calls with arguments, Ledger version, today's date); the date is there so "last month" is never read against another day. The intent is the routed sub-graph for now; no embedding is used. Only Ask answers that passed grounding after a single round of tool calls are stored, so those calls are everything the answer read. The graph looks the key up as soon as the model has chosen that round's calls, so a hit skips the tools, the answer generation and grounding; the reply's `path` is `cache`, and it carries the evidence IDs stored with the answer (ADR 0007). Deterministic fallbacks and Proposals are never cached. Storing drops rows of older Ledger versions, and a confirmed or deleted Memory clears the user's cache.

## Privacy gateway

Separate module, no database access. Placeholders replace figures, merchants and personal values; only allowlisted fields leave; per-conversation opt-in; every payload logged and viewable; replies validated before placeholders are filled locally; hard £9 monthly cap shared with the aggregator (ADR 0011, ADR 0013).

Notes (#124) are left out by default. Transaction rows carry no note text; notes are read only through the Ledger's `note` methods (`/api/notes`). Any context builder or export that could feed the gateway (the Assistant tools, #136) takes `exclude_notes: bool = True` and includes notes only when the user opts in for that one question.

## Intelligence Engine

**Detectors** (pure functions, Hypothesis-tested): recurring payments, price increases, Category spikes against personal baseline, unused subscriptions, upcoming renewals, cash-flow shortfall risk, life changes (change-point on income and essentials).

**Ranking**: impact × confidence × relevance, then a feasibility filter against Memories and constraints. Baseline to beat: rank by £ impact alone.

**Forecasting**: recurring payments from their schedule; discretionary spending per Category via exponential smoothing (ETS) with prediction intervals; cash flow combines both.

**Allocation Plans**: the Plan sub-graph gathers constraints and priorities as Memory Proposals; a linear program (`scipy.optimize`, OR-Tools only if needed) splits income with constraints as hard limits and weights from learned preferences; compared with a 50/30/20 baseline; explained with trade-offs.

**Playbook** (#254, `engine.playbook`; it replaces the knowledge base, which the user dropped on 8 Nov 2026, so nothing is fetched or embedded): short guidance cards we write ourselves, versioned in the repo as data (`engine/playbook_data/cards/<id>.json`). A card has an id, a topic, principles, gentle and blunt variants, and a "when this applies" condition (`all`/`any` over `{signal, op, value}` clauses) on named Engine signals computed from the Ledger's Spending rows and savings balance: essential monthly spend (Bills, Groceries, Pharmacy, public transport and fuel over the last three complete months), savings in months of essentials (floored to 0.1, so the figure shown is the one the condition read), and Subscriptions spend with its run of monthly rises. A signal without enough history is `None`, which fails its clause, and conditions have no negation, so a card never shows on missing data. Titles and bodies name slots, `{signal}` or `{fact:id}`, filled only by Engine figures or a statutory fact. A fact (`playbook_data/facts.json`) carries a source URL, never fetched, and a "checked on" date, always shown; one checked before the current tax year began (6 April) shows "may be out of date" (#251 decision 6), and CI fails one over 13 months old. Loading fails on an unknown signal, slot, fact or key; a digit outside a slot (any script), a character outside printable ASCII and a few typographic marks, or instruction-like text in card prose fails it too, and the lint runs in CI. Evaluation: `evals/playbook.py` on ten seeded personas (`evals/synthetic_personas.py`) with gold "cards that apply": per-card precision and recall 1.00 against show-every-card (emergency fund 0.60, subscription creep 0.20 precision) and show-none (recall 0), `figures_from_engine` 1.0 and no card shown with its condition false. The cards are the version recorded with the results (`cards-<sha>`).

## Learning about the user

Layered, each step shipped only if it beats the previous one on evaluations:

1. Supervised categorisation from Corrections.
2. Personal baselines (seasonal, payday cycle).
3. Beta-Bernoulli relevance per Insight type and Category. As built (#150): `engine.insights` runs the detectors over the Ledger's GBP Spending (rows needing an FX rate left out; cancelled and not-a-subscription Merchants `stopped`), keeps what was raised in the 90 days up to the last row, and stores it idempotently (`ledger.insights`). The feed (`GET /api/insights`) ranks by impact × confidence × relevance, then drops any Insight whose Merchant or Category shares a word with a constraint Memory's tag. Only thumbs-up, thumbs-down and dismissals move the posterior; listing never does.
4. Weekly worth-it / regret review. As built (#152): `engine.briefing` picks 3 discretionary Transactions a week (Spending from the 28 days before the week, never Income, Transfers, Refunds, Fees or a Merchant with a schedule or registry entry, and never one answered in another week), ordered by SHA-256 of user id, week and Transaction id, so a week's list is stable. Answers are feedback (`regret_answer`), never Memories. The weekly briefing (`GET /api/briefing`) adds the top 3 ranked Insights, the forecast band for the week's month and the next payday (`engine.forecast`), and upcoming renewals; it says "insufficient" for a part without enough history, never a made-up figure.
5. Pairwise "which would you cut" questions: Bradley-Terry with active learning.
6. Thompson-sampling contextual bandit for what to suggest and when (from ~50 ratings).
7. Change-point detection resets baselines after confirmed life changes.

Not planned: full reinforcement learning (insufficient data), fine-tuning on real data (ADR 0009). LoRA via MLX only on synthetic tool-calling data if the benchmark shows a gap.

New model versions run in shadow, compared on the most recent month, promoted only if better, with one-click rollback via the model registry.
