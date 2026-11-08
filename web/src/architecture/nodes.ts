/* The /architecture page's content. Every statement here is taken from a file listed in its node's
   `sources` (paths from the repo root; nodes.test.ts checks each one exists) or from a GitHub issue
   in `issues`. When the code changes, change this file with it. */

export type Status = "built" | "shadow" | "planned";

export const STATUS_LABEL: Record<Status, string> = {
  built: "Built",
  shadow: "In shadow",
  planned: "Planned",
};

export type GroupId = "flow" | "knowledge" | "models" | "context" | "learning" | "evals" | "security";

export interface Group {
  id: GroupId;
  title: string;
  lede: string;
}

export interface ArchNode {
  id: string;
  group: GroupId;
  /** parts this one feeds, reads or guards: the map's edges (undirected on screen) */
  links: string[];
  title: string;
  /** one line, shown on the node itself */
  short: string;
  decision: string;
  why: string;
  rejected: string[];
  status: Status;
  /** what is true today when the status needs a qualifier */
  note?: string;
  sources: string[];
  issues?: number[];
}

export const REPO = "https://github.com/esharif20/finance-agent";

export const GROUPS: Group[] = [
  {
    id: "flow",
    title: "One question, start to finish",
    lede: "Follow one open question through five stops. A check holds every figure to a tool result before anything reaches the screen; open any stop for the decision behind it.",
  },
  {
    id: "knowledge",
    title: "What it knows, in four tiers",
    lede: "Vectors resolve meaning; SQL holds facts. A top-k search cannot sum every matching row exactly, so totals always come from the Ledger.",
  },
  {
    id: "models",
    title: "Which model answers",
    lede: "A small model on the Mac by default. The cloud only words a reply, only when the user opts in for that conversation, and only ever sees placeholders.",
  },
  {
    id: "context",
    title: "Context order and caching",
    lede: "Each turn's prompt is assembled fresh, in a fixed order, so the stable part is reused from the model's cache and only the new tokens are processed.",
  },
  {
    id: "learning",
    title: "How it learns about one person",
    lede: "Sized to one person's data: a few thousand Transactions a year and about one feedback signal a day. Small-data statistics and bandits, no full reinforcement learning.",
  },
  {
    id: "evals",
    title: "Evaluations and CI gates",
    lede: "Every intelligence ticket writes its evaluation first: dataset, metric and a baseline to beat. Synthetic evaluations run in CI and block a merge on regression.",
  },
  {
    id: "security",
    title: "What it can never do",
    lede: "Real data stays on the Mac, encrypted. Three hosts can be reached, and nothing in the app can move money.",
  },
];

export const NODES: ArchNode[] = [
  // ---- the question flow
  {
    id: "planner",
    group: "flow",
    links: ["tools", "cache"],
    title: "Planner",
    short: "References resolved in code, 0 model calls",
    decision:
      "Code resolves every reference in the question against the user's own data: periods, Categories through a synonym map (food is Groceries plus Eating out), Merchants, Accounts, \"big\" payments, Subscriptions, income and people through Memories. The result is a typed Plan. Figure questions, balances and follow-ups (\"and the month before?\", \"by merchant\") are answered from a template with no model call; a follow-up patches the thread's last Plan.",
    why: "The model resolves nothing, so it can't pick the wrong month or Merchant, and every choice is shown back as a \"Read as\" line the user can check. On the synthetic conversation bench, figure and follow-up turns take 0 model calls and well under a second.",
    rejected: [
      "Letting the model choose the tool and read the dates: a model told the date mixed months up.",
      "Guessing an ambiguous reference: when the choice would change the figure, it asks once instead.",
      "Planning a question with any word no resolver read: that goes to the model, never a wrong plan.",
    ],
    status: "built",
    note: "Resolving Categories and Merchants by meaning (embeddings with FTS5) is the next step in #317.",
    sources: [
      "src/finance_agent/assistant/planner.py",
      "src/finance_agent/assistant/resolve.py",
      "evals/assistant_conversations.py",
      "evals/results/assistant_conversations_local.json",
    ],
    issues: [317],
  },
  {
    id: "tools",
    group: "flow",
    links: ["writer", "l1", "l2", "l3"],
    title: "One round of Engine reads",
    short: "Up to 3 read-only calls, chosen by code",
    decision:
      "An open or in-depth question gets one fetch round of at most 3 read-only Engine calls, chosen by the planner: for \"where can I save?\", the month's spending, three Guidance cards and three Insights. Each result is an aggregate with an evidence ID on every row.",
    why: "Plan-then-execute replaces a loop where the model calls tools round by round. The research adopted in #317 cites LLMCompiler's report of up to 3.7 times lower latency than ReAct.",
    rejected: [
      "A ReAct loop, one model call per tool round.",
      "Cross-encoder rerankers on search hits: too heavy for 16 GB alongside the model.",
    ],
    status: "built",
    note: "The three calls run one after another on one connection, since SQLite reads take milliseconds; running them in parallel is planned if the bench shows the round past 1 s.",
    sources: ["src/finance_agent/assistant/planner.py", "src/finance_agent/assistant/graph.py"],
    issues: [317],
  },
  {
    id: "writer",
    group: "flow",
    links: ["grounding", "order", "local", "cloud"],
    title: "One writer call",
    short: "No tool schemas, the model only writes",
    decision:
      "One model call with no tool schemas words the answer from the fetched facts, given as compact lines just before the question: the answer first, then the two or three facts that matter, with advice only from the Guidance and Insights it was given. The local model runs greedy (temperature 0, thinking off), with the same 8,192-token context every call and at most 400 output tokens.",
    why: "Fetching is done by code, so the model can't call the wrong tool or loop. On the conversation bench the 4b answers open and in-depth questions in one call, inside a 10 s p95.",
    rejected: [
      "Streaming tokens as they are generated: a figure is checked only once the whole reply exists, and a reply that fails is replaced, so early tokens could show figures that are then withdrawn.",
      "The model's own sampling defaults (qwen3.5 ships temperature 1).",
    ],
    status: "built",
    sources: ["src/finance_agent/assistant/graph.py", "src/finance_agent/assistant/context.py", "src/finance_agent/assistant/ollama.py"],
    issues: [317],
  },
  {
    id: "grounding",
    group: "flow",
    links: ["reply", "cache"],
    title: "Grounding check",
    short: "Every figure matches a tool result",
    decision:
      "Every £ figure, percentage and date in a reply must match a value from a tool result in that turn. The sign is exact, a month in words counts as a date, and every other number fails closed. A failure is regenerated once, with no tools, then replaced by a deterministic table.",
    why: "The Engine computes; the Assistant explains (ADR 0007). Letting the model reason about money directly would be simpler, but makes figures unverifiable.",
    rejected: [
      "Letting the LLM compute or round figures itself.",
      "Loose matching: \"you saved £40\" must not pass against a net of -£40.",
    ],
    status: "built",
    sources: [
      "src/finance_agent/assistant/grounding.py",
      "src/finance_agent/assistant/values.py",
      "docs/adr/0007-engine-computes-assistant-explains.md",
    ],
  },
  {
    id: "reply",
    group: "flow",
    links: [],
    title: "Reply and evidence",
    short: "Streamed only after the check",
    decision:
      "The reply streams to the screen only after it passes grounding. It carries the evidence IDs of the rows it read, a \"Read as\" line for how code resolved the question, and which path answered (planner, local model, cache or the deterministic table), with its time and number of model calls.",
    why: "No unchecked figure ever reaches the screen, and every figure can be traced back to the rows behind it.",
    rejected: ["Showing the reply as it is generated, before the check."],
    status: "built",
    sources: ["src/finance_agent/assistant/evidence.py", "src/finance_agent/api/chat.py", "docs/AI_DESIGN.md"],
  },

  // ---- knowledge tiers
  {
    id: "l0",
    group: "knowledge",
    links: ["l1"],
    title: "L0 Profile",
    short: "What it always knows, in under 800 tokens",
    decision:
      "A typed profile built entirely by the Engine, every figure in minor units: Accounts and balances, income and the next payday, bills and subscriptions, each Category's median month, trends, unusual recent payments, and goals and Memories by id and tag only. It renders as compact text in a fixed order, kept in memory and keyed by the Ledger version, so a stale one is never served.",
    why: "The Assistant starts from the shape of the user's finances without a search, and a stable prefix is reused from the KV cache. On three synthetic Personas all 373 facts check out and the text grounds; it is 329 tokens at most, against 927 for the same profile as JSON.",
    rejected: [
      "A JSON dump of the same facts: nearly three times the tokens.",
      "Re-searching the Ledger for the basics on every question.",
    ],
    status: "built",
    note: "The writer call puts it straight after the system prefix and grounds against its figures. A cloud call sends none of it.",
    sources: ["src/finance_agent/engine/profile.py", "src/finance_agent/assistant/graph.py", "docs/ML_EVALUATIONS.md"],
    issues: [327],
  },
  {
    id: "l1",
    group: "knowledge",
    links: ["l3"],
    title: "L1 Summaries",
    short: "Pre-computed tables, exact, milliseconds",
    decision:
      "Materialised summaries (month by Category by Merchant, baselines, recurring series, balances), rebuilt incrementally for only the months a sync or Correction touched.",
    why: "Open questions read a ready summary instead of recomputing it, and a stale summary is never served.",
    rejected: ["Recomputing month summaries from raw rows inside a turn."],
    status: "planned",
    note: "Built today: one stored recap a month, with the fingerprint of the data it was built from; its figures are recomputed on every read.",
    sources: ["src/finance_agent/ledger/stories.py"],
    issues: [327],
  },
  {
    id: "l2",
    group: "knowledge",
    links: ["l3", "local"],
    title: "L2 Meaning index",
    short: "FTS5 and local embeddings, fused",
    decision:
      "Hybrid search over Transactions, Merchants, Notes, Split parts and Memories: SQLite FTS5 ranked by BM25, and nomic-embed-text vectors from Ollama on localhost, fused by reciprocal rank fusion, then a deterministic rerank.",
    why: "Words like \"coffee\" or a misspelt Merchant need meaning, not exact match. The vectors only find things; they never produce a total.",
    rejected: ["A vector database of every transaction as the source of answers."],
    status: "built",
    note: "Merchant-month documents and sqlite-vec are planned in #327.",
    sources: ["src/finance_agent/engine/search.py", "src/finance_agent/ledger/search_index.py"],
    issues: [327],
  },
  {
    id: "l3",
    group: "knowledge",
    links: [],
    title: "L3 Raw Ledger",
    short: "SQL, the exact drill-down",
    decision:
      "The Ledger itself, read through SQL scoped to one user: every Transaction, Correction and Split. It is the path a question falls through to when nothing above holds the answer.",
    why: "Exact sums need every matching row. SQL holds the facts (ADR 0007).",
    rejected: ["Summing the top-k hits of a search."],
    status: "built",
    sources: ["src/finance_agent/ledger/__init__.py", "docs/ARCHITECTURE.md", "docs/adr/0007-engine-computes-assistant-explains.md"],
    issues: [327],
  },

  // ---- models and the router
  {
    id: "local",
    group: "models",
    links: [],
    title: "Local models",
    short: "qwen3.5 9b by default, 4b for speed",
    decision:
      "The default is qwen3.5:9b-q4_K_M through Ollama on localhost. Settings offers the 4b, about 1.7 times quicker at p50 and 4.5 GB rather than 6.8 GB. Switching unloads the old model so two never sit in memory.",
    why: "The rule was to default to the 4b only if its grounding came within 0.05 of the 9b's; it scored 0.44 against 0.68. The 9b is the smallest model that refuses all six unsafe asks.",
    rejected: [
      "The nvfp4 builds on Ollama's MLX runner: memory grew from about 4–5 GB after one question to about 12 GB after 25.",
      "gemma4:e4b (grounding 0.42) and lfm2.5:8b (routing 0.44).",
    ],
    status: "built",
    sources: ["docs/AI_DESIGN.md", "src/finance_agent/assistant/ollama.py", "evals/results/assistant_local.json"],
  },
  {
    id: "router",
    group: "models",
    links: ["planner", "local", "proposals"],
    title: "Router",
    short: "Keywords first, then one word from the model",
    decision:
      "A keyword pre-router sends obvious messages (\"how much\", \"what did\", \"remember …\") to their sub-graph with no model call. Anything about the future, a write or a payment always goes to the model Router, which answers one word; anything unclear goes to Ask, the read-only sub-graph.",
    why: "On golden set v3 the Router call was skipped on 42% of turns, and a refused call to another sub-graph's tool comes back as a fixed error, never its text.",
    rejected: ["A model call to route every message."],
    status: "built",
    note: "A learned router (#284, #285) is planned; the exposure log already records a route's quantised features, never the question.",
    sources: ["src/finance_agent/assistant/graph.py", "src/finance_agent/ledger/exposure.py", "docs/AI_DESIGN.md"],
  },
  {
    id: "cloud",
    group: "models",
    links: ["placeholders", "egress"],
    title: "Cloud wording",
    short: "Opt-in per conversation, placeholders only",
    decision:
      "Only in a conversation the user opted in to, and only after the tools ran locally: the gateway redacts the turn to placeholders, the cost meter reserves before the call, Gemini Flash words the reply, the reply is validated, the real values are filled in on the Mac, and grounding runs unchanged.",
    why: "Cloud prose can be clearer, but is only as private as its placeholders. The key must belong to a paid-tier project, checked at startup, because on the free tier prompts may be used to improve Google's products.",
    rejected: [
      "Provider SDKs: none may be imported anywhere; the client uses the standard library.",
      "Sending figures, Merchants, exact dates or Memory text.",
    ],
    status: "built",
    note: "The committed benchmark rows come from an offline stand-in; the real-key run is #306.",
    sources: [
      "src/finance_agent/assistant/cloud_route.py",
      "src/finance_agent/gateway/cloud.py",
      "docs/adr/0011-cloud-models-see-placeholders-only.md",
      "docs/adr/0016-hosted-demo-and-cloud-egress.md",
    ],
  },

  // ---- context order and caching
  {
    id: "order",
    group: "context",
    links: ["l0", "warm"],
    title: "Append-only prompt",
    short: "Prefix, profile, Memories, summary, turns, facts",
    decision:
      "The writer's prompt has a fixed order: the system prefix, the L0 profile, the Memories relevant to the question, a summary of older turns written by code (topics only, no figures), the last 3 turns, this turn's facts, then the question. Digits in earlier replies are masked; Memories and bank text enter as quoted data.",
    why: "The prefix stays the same through a conversation and the rest follows it in order, so Ollama reuses its KV cache for the prefix; the 3-turn cap bounds how the prompt grows.",
    rejected: [
      "Accumulating the transcript: each turn's context is assembled, never accumulated.",
      "A model call to summarise older turns.",
      "Speculative decoding: measured 11–24% slower on M1-class llama.cpp Metal.",
    ],
    status: "built",
    note: "Today's date still sits in the system prefix.",
    sources: ["src/finance_agent/assistant/context.py", "docs/AI_DESIGN.md"],
    issues: [317],
  },
  {
    id: "warm",
    group: "context",
    links: ["local"],
    title: "Warm model",
    short: "Loaded while the chat is open",
    decision:
      "Opening the chat loads the model with the same context size the turns use, and every call keeps it for 30 minutes. Closing the chat screen unloads it.",
    why: "The first turn skips the model load (5.9 s at p50 for the 9b on golden set v3), a changed context size would force a reload, and the memory comes back as soon as the user is done.",
    rejected: ["Loading the model on the first question, which added the load to that turn."],
    status: "built",
    sources: ["src/finance_agent/assistant/ollama.py", "src/finance_agent/api/chat.py", "docs/AI_DESIGN.md"],
    issues: [317],
  },
  {
    id: "cache",
    group: "context",
    links: ["reply"],
    title: "Answer cache",
    short: "Keyed by the data, never by wording",
    decision:
      "The key is a SHA-256 of the intent, the tool calls with their arguments, the Ledger version and today's date. Only answers that passed grounding are stored, encrypted; a hit skips the tools, the model and the check, and still carries its evidence IDs.",
    why: "A new sync bumps the Ledger version, so a cached answer can never describe old data, and \"last month\" is never read against another day.",
    rejected: [
      "Returning a cached answer by embedding similarity (ADR 0010).",
      "Caching deterministic fallbacks or Proposals.",
    ],
    status: "built",
    sources: ["src/finance_agent/ledger/answer_cache.py", "docs/adr/0010-cache-keyed-by-data-state.md"],
  },

  // ---- the learning loop
  {
    id: "bandit",
    group: "learning",
    links: ["tools", "beta"],
    title: "Contextual Thompson bandit",
    short: "Chooses what to suggest, in shadow",
    decision:
      "Over Insights and playbook cards: each (type, Category, time of month, recent spend) cell has a Beta posterior, pooled with the item's evidence from other cells. One seeded draw per arm fills a slate capped at 3 items a week, and every shown item logs its propensity.",
    why: "In shadow it logs what it would show while the live ranker keeps serving. It goes live only if off-policy evaluation (IPS and SNIPS) on the logged propensities beats the ranker with a confidence lower bound above 0.",
    rejected: [
      "Full reinforcement learning: rewards are sparse and months late, and there are no money-moving actions to learn.",
      "Opens, clicks or time on screen as rewards: only acceptances, acted-on outcomes and regret reviews count.",
    ],
    status: "shadow",
    sources: [
      "src/finance_agent/engine/bandit.py",
      "src/finance_agent/engine/ope.py",
      "src/finance_agent/ledger/exposure.py",
      "docs/adr/0015-learning-sized-to-one-persons-data.md",
    ],
    issues: [327],
  },
  {
    id: "bt",
    group: "learning",
    links: ["tools"],
    title: "Bradley-Terry preferences",
    short: "\"Which would you cut first?\"",
    decision:
      "Pairwise answers give each Category, card or goal a strength, with P(a beats b) = sigmoid(s_a − s_b), fitted as the MAP estimate under a Gaussian prior. The next pair asked is the one whose answer adds the most information.",
    why: "It starts from its prior with zero answers and needs no minimum-data gate, and a few well-chosen questions say more than many random ones.",
    rejected: ["Random pairs, or asking a pair again before every pair has been asked."],
    status: "built",
    note: "Feeding the learned strengths into the L0 profile, so advice can cite them, is planned in #327.",
    sources: ["src/finance_agent/engine/preferences.py", "src/finance_agent/ledger/pairwise.py"],
    issues: [327],
  },
  {
    id: "beta",
    group: "learning",
    links: ["tools"],
    title: "Beta relevance",
    short: "Thumbs move it, nothing else",
    decision:
      "Insights rank by impact × confidence × relevance, where relevance is a Beta(1, 1) posterior per (Insight type, Category) moved only by a thumbs-up, a thumbs-down or a dismissal. A constraint Memory can then rule an Insight out.",
    why: "On the synthetic replay it beats ranking by £ impact alone: NDCG@3 0.838 against 0.802, winning on five of six users.",
    rejected: ["Views or clicks as signal (ADR 0015)."],
    status: "built",
    sources: ["src/finance_agent/engine/ranking.py", "docs/ML_EVALUATIONS.md"],
  },
  {
    id: "lora",
    group: "learning",
    links: ["planner", "bench"],
    title: "LoRA fine-tunes",
    short: "Small, local, eval first",
    decision:
      "A LoRA via MLX, on the Mac only, for a 0.5–1.5B planner trained on synthetic paraphrases and the golden set; and a small categoriser head on the embeddings, trained on the user's own Corrections.",
    why: "Each is measured before training: the planner on exact-plan match and latency against the prompted 4b, the head on accuracy against kNN. Either ships only if it beats its baseline.",
    rejected: ["Fine-tuning a model on real data (ADR 0009)."],
    status: "planned",
    sources: ["docs/adr/0009-no-fine-tuning-on-real-data.md", "docs/AI_DESIGN.md"],
    issues: [327],
  },

  // ---- evals and CI gates
  {
    id: "bench",
    group: "evals",
    links: ["router", "tools", "grounding", "local"],
    title: "Assistant benchmark",
    short: "Golden set, scored per model",
    decision:
      "Synthetic golden questions with the expected route, tool calls and figures. Each model is scored on routing, tools, grounding, refusals, latency and peak memory on the M1 Pro, and the fast-path cases must make 0 model calls.",
    why: "Model choice is benchmark-driven: the smallest model that passes the accuracy gates within about 7 GB of memory.",
    rejected: ["Defaulting to the 4b for speed: it missed the grounding rule (see Local models)."],
    status: "built",
    sources: ["evals/assistant_bench.py", "evals/results/assistant_fake.json", "docs/ML_EVALUATIONS.md"],
  },
  {
    id: "gate",
    group: "evals",
    links: ["bench"],
    title: "Results gate",
    short: "Committed results must reproduce",
    decision:
      "CI fails unless the committed results equal a fresh run, and writing new results refuses to lower any committed metric, or to change a dataset without a version bump, unless the lower result is explicitly accepted.",
    why: "Fixed seeds and versioned datasets make every number on this page reproducible, and a regression cannot slip in quietly.",
    rejected: ["Results that can be rewritten lower without saying so."],
    status: "built",
    sources: ["evals/gate.py", ".github/workflows/ci.yml"],
  },
  {
    id: "judge",
    group: "evals",
    links: ["cloud", "gate"],
    title: "Calibrated judge",
    short: "Agreement with hand labels first",
    decision:
      "A Gemini Pro judge scores cloud replies from 1 to 5 on a fixed rubric, in strict JSON. It is calibrated first on 30 hand-labelled replies: Cohen's κ must reach 0.6, or the results say uncalibrated and carry no scores.",
    why: "A judge that disagrees with people measures nothing. The offline stand-in reaches κ 0.65.",
    rejected: ["An uncalibrated LLM judge."],
    status: "built",
    note: "Gemini Pro's own κ comes from the real-key run, #306.",
    sources: ["evals/judge.py", "evals/results/judge.json"],
  },
  {
    id: "ci",
    group: "evals",
    links: ["gate", "egress"],
    title: "CI jobs",
    short: "One aggregate check guards main",
    decision:
      "Parallel jobs for lint (gitleaks, the fixture guard, ruff, pyright, the import-linter contract), Python tests, evals, the web build and tests, and WebKit smoke tests. One aggregate check requires every job to have succeeded.",
    why: "main is protected: nothing merges on a red job, and fixtures must be synthetic, checked by the fixture guard and gitleaks.",
    rejected: ["Real-data evaluations in CI: they run only on the Mac, and their metrics are never committed."],
    status: "built",
    sources: [".github/workflows/ci.yml", "scripts/fixture_guard.py"],
  },

  // ---- security
  {
    id: "egress",
    group: "security",
    links: [],
    title: "Three hosts",
    short: "Ollama, one aggregator, Gemini",
    decision:
      "The app may reach Ollama on localhost, one pinned read-only SimpleFIN host (only from the ingest client), and generativelanguage.googleapis.com (only from the gateway). A test scans every source file so no other module can import a network client.",
    why: "Each host is in an ADR. Adding one needs a new ADR and the user's explicit approval. No LangSmith and no third-party tracing.",
    rejected: ["Provider SDKs and their telemetry.", "Any further host without a new ADR."],
    status: "built",
    sources: [
      "tests/test_egress.py",
      "tests/test_network_boundary.py",
      "docs/adr/0013-one-read-only-aggregator-and-9-pound-cap.md",
      "docs/adr/0016-hosted-demo-and-cloud-egress.md",
    ],
  },
  {
    id: "keychain",
    group: "security",
    links: ["sqlcipher", "cloud"],
    title: "Keychain",
    short: "Every secret, never in a file",
    decision:
      "The database key, the account-reference HMAC key, the SimpleFIN token and the Gemini key (under its own service name) live in the macOS Keychain, never in files or committed environment variables.",
    why: "A stolen repo or copied config holds nothing that opens the data.",
    rejected: ["Keys in .env files or the demo's host."],
    status: "built",
    sources: ["docs/DATA_POLICY.md", "docs/THREAT_MODEL.md"],
  },
  {
    id: "sqlcipher",
    group: "security",
    links: ["l3", "cache"],
    title: "Encrypted database",
    short: "SQLCipher, before any real data",
    decision:
      "The Ledger, the graph's checkpoints and the answer cache live in one SQLCipher database whose key is in the Keychain. Backups go to a registered external drive, still encrypted.",
    why: "With FileVault, a stolen laptop or imaged disk gives up nothing without the key (ADR 0002).",
    rejected: ["Plain SQLite for real data.", "Backups to the cloud."],
    status: "built",
    sources: ["src/finance_agent/storage/__init__.py", "docs/adr/0002-no-real-data-before-sqlcipher.md"],
  },
  {
    id: "placeholders",
    group: "security",
    links: ["egress"],
    title: "Gateway placeholders",
    short: "{P1}, {M1}, {D1} leave; values stay",
    decision:
      "Figures become {Pn}, Merchants {Mn}, dates {Dn} and the user's own Categories {Cn}. A final check refuses any payload whose free text still names a mapped value. The reply must be words and known placeholders only, and is filled in locally.",
    why: "A bug in redaction fails closed instead of leaking, and every payload is logged where the user can read it.",
    rejected: ["Sending amounts, Merchants, exact dates, account details or Memory text."],
    status: "built",
    sources: [
      "src/finance_agent/gateway/redact.py",
      "src/finance_agent/gateway/validate.py",
      "docs/adr/0011-cloud-models-see-placeholders-only.md",
    ],
  },
  {
    id: "injection",
    group: "security",
    links: ["order", "l3"],
    title: "Injection sanitising",
    short: "Bank text is data, never instructions",
    decision:
      "Bank-supplied text is folded to printable ASCII, so zero-width, bidi and lookalike characters cannot hide a phrase, then cut at the first instruction-like marker and kept to 60 characters. It reaches the model quoted as data.",
    why: "A Merchant descriptor or CSV cell is the easiest way to put words in front of the model. A CI injection suite covers descriptors, CSV cells and chat.",
    rejected: ["Trusting the model to ignore instructions in bank text."],
    status: "built",
    sources: ["src/finance_agent/sanitise.py", "tests/test_assistant_injection.py"],
  },
  {
    id: "nopay",
    group: "security",
    links: ["egress", "proposals"],
    title: "No payments",
    short: "No transfer capability exists",
    decision: "No payment or transfer capability exists anywhere in the app. Bank access is read-only.",
    why: "What does not exist cannot be prompted into running.",
    rejected: ["Payment initiation over Open Banking: bank access stays read-only."],
    status: "built",
    sources: ["docs/THREAT_MODEL.md", "docs/adr/0015-learning-sized-to-one-persons-data.md"],
  },
  {
    id: "proposals",
    group: "security",
    links: ["reply"],
    title: "Proposals only",
    short: "Every write waits for a click",
    decision:
      "The Assistant's only writes are Proposals: a recategorisation, a transfer mark, a Memory. The graph pauses on a confirm card until the user answers. A Memory can't hold a money figure.",
    why: "The user stays the only one who changes their data, and a bad Memory can't quietly steer advice.",
    rejected: ["Write tools the model can call directly."],
    status: "built",
    sources: ["src/finance_agent/assistant/tools/propose.py", "src/finance_agent/ledger/proposals.py"],
  },
];

/** the sample question's path through the flow, in order */
export const FLOW = NODES.filter((n) => n.group === "flow");

export const SAMPLE = {
  question: "Where can I save this month?",
  steps: [
    "Code reads \"this month\" as its exact dates and plans the turn; the model resolves nothing.",
    "One round of Engine reads: the month's spending, three Guidance cards and three Insights.",
    "One model call, offered no tools, writes the answer first and then the facts that matter.",
    "Each figure is matched to a tool value from this turn; a miss means one retry, then the table.",
    "The checked reply streams, with the evidence behind every figure.",
  ],
} as const;
