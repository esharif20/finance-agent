/* The page's flowcharts and small diagrams. Every box says what docs/ARCHITECTURE.md and the
   node sources in nodes.ts say; no figure here is real data. */
import { useInView } from "motion/react";
import { type ReactNode, useRef } from "react";
import Chart, { type CEdge, type Layout } from "./Chart";

/* ---------------------------------------------------------------- the system, in the opening */

const TIPS = {
  simplefin: "One pinned, read-only aggregator host, reached only by the ingest client.",
  csv: "Statement exports, imported from a file on the Mac.",
  ingest: "One parser per source; account numbers are masked before anything is stored.",
  ledger: "Every Transaction in integer pence, in one SQLCipher database keyed from the Keychain.",
  engine: "Computes every figure: baselines, forecasts, Insights and the L0 profile.",
  assistant: "Plans in code, fetches from the Engine, writes once, checks every figure.",
  app: "The React app on localhost, the only place a reply is shown.",
  ollama: "The local model on localhost. The default, and the only model unless you opt in.",
  gateway: "Redacts a turn to placeholders before anything leaves, and checks it again.",
  gemini: "Words a reply in a conversation you opted in to. Sees placeholders only.",
};

const SYS_EDGES: CEdge[] = [
  { from: "simplefin", to: "ingest" },
  { from: "csv", to: "ingest" },
  { from: "ingest", to: "ledger" },
  { from: "ledger", to: "engine", label: "pence" },
  { from: "app", to: "assistant", label: "question" },
  { from: "engine", to: "assistant", label: "tool results" },
  { from: "assistant", to: "ollama", label: "1 call" },
  { from: "assistant", to: "gateway", dashed: true },
  { from: "gateway", to: "gemini", dashed: true },
];

const SYS_WIDE: Layout = {
  vb: [1000, 420],
  zones: [{ x: 190, y: 16, w: 640, h: 388, label: "Your Mac" }],
  nodes: [
    { id: "simplefin", x: 6, y: 186, w: 160, label: "SimpleFIN", sub: "read-only feed", tip: TIPS.simplefin, outside: true },
    { id: "csv", x: 212, y: 60, w: 130, label: "CSV import", sub: "your export", tip: TIPS.csv },
    { id: "ingest", x: 212, y: 186, w: 130, label: "Ingest", sub: "parse, mask", tip: TIPS.ingest },
    { id: "ledger", x: 376, y: 186, w: 130, label: "Ledger", sub: "SQLCipher", tip: TIPS.ledger, solid: true },
    { id: "engine", x: 376, y: 320, w: 130, label: "Engine", sub: "every figure", tip: TIPS.engine },
    { id: "assistant", x: 600, y: 320, w: 150, label: "Assistant", sub: "plan, write, check", tip: TIPS.assistant, solid: true },
    { id: "app", x: 600, y: 186, w: 150, label: "The app", sub: "localhost", tip: TIPS.app },
    { id: "ollama", x: 410, y: 60, w: 130, label: "Ollama", sub: "local model", tip: TIPS.ollama },
    { id: "gateway", x: 640, y: 60, w: 160, label: "Privacy gateway", sub: "placeholders", tip: TIPS.gateway },
    { id: "gemini", x: 856, y: 60, w: 138, label: "Gemini", sub: "opt-in only", tip: TIPS.gemini, outside: true },
  ],
};

const SYS_TALL: Layout = {
  vb: [400, 830],
  zones: [{ x: 8, y: 92, w: 384, h: 640, label: "Your Mac" }],
  nodes: [
    { id: "simplefin", x: 120, y: 12, w: 160, label: "SimpleFIN", sub: "read-only feed", tip: TIPS.simplefin, outside: true },
    { id: "ingest", x: 120, y: 136, w: 160, label: "Ingest", sub: "parse, mask", tip: TIPS.ingest },
    { id: "ledger", x: 120, y: 236, w: 160, label: "Ledger", sub: "SQLCipher", tip: TIPS.ledger, solid: true },
    { id: "engine", x: 120, y: 336, w: 160, label: "Engine", sub: "every figure", tip: TIPS.engine },
    { id: "assistant", x: 120, y: 436, w: 160, label: "Assistant", sub: "plan, write, check", tip: TIPS.assistant, solid: true },
    { id: "app", x: 24, y: 560, w: 150, label: "The app", sub: "localhost", tip: TIPS.app },
    { id: "ollama", x: 226, y: 560, w: 150, label: "Ollama", sub: "local model", tip: TIPS.ollama },
    { id: "gateway", x: 120, y: 656, w: 160, label: "Privacy gateway", sub: "placeholders", tip: TIPS.gateway },
    { id: "gemini", x: 120, y: 764, w: 160, label: "Gemini", sub: "opt-in only", tip: TIPS.gemini, outside: true },
  ],
};

export function SystemChart({ wide, live }: { wide: boolean; live: boolean }) {
  return (
    <Chart
      className="ar-chart-system"
      title="The system: a bank feed and CSV imports go through ingest into the Ledger; the Engine computes from it; the Assistant answers the app with a local model, and only with your opt-in sends placeholders through the privacy gateway to Gemini. Everything but SimpleFIN and Gemini runs on your Mac."
      layout={wide ? SYS_WIDE : SYS_TALL}
      edges={SYS_EDGES}
      active={[0, 2, 3, 4, 5, 6]}
      live={live}
    />
  );
}

/* ---------------------------------------------------------------- the question, with its fallback */

const Q_EDGES: CEdge[] = [
  { from: "planner", to: "fetch" },
  { from: "fetch", to: "writer" },
  { from: "writer", to: "check" },
  { from: "check", to: "reply", label: "pass" },
  { from: "check", to: "retry", label: "miss" },
  { from: "retry", to: "reply", label: "pass", via: "h" },
  { from: "retry", to: "table", label: "miss again" },
];

const Q_LAYOUT: Layout = {
  vb: [440, 290],
  nodes: [
    { id: "planner", x: 4, y: 8, w: 128, label: "Planner", sub: "code, no model", tip: "References resolved against your own data; picks the Engine calls." },
    { id: "fetch", x: 156, y: 8, w: 128, label: "Fetch", sub: "≤ 3 reads", tip: "One round of read-only Engine calls; every row carries an evidence ID." },
    { id: "writer", x: 308, y: 8, w: 128, label: "Writer", sub: "1 model call", tip: "Offered no tools: it can only word the facts it was given." },
    { id: "check", x: 308, y: 118, w: 128, label: "Grounding", sub: "every figure", tip: "Each £, % and date must match a tool value from this turn." },
    { id: "reply", x: 308, y: 228, w: 128, label: "Reply", sub: "with evidence", tip: "Streams only after the check passes.", solid: true },
    { id: "retry", x: 120, y: 118, w: 138, label: "Retry once", sub: "no tools", tip: "A failed reply is regenerated once, with the same facts." },
    { id: "table", x: 120, y: 228, w: 138, label: "Plain table", sub: "deterministic", tip: "If the retry fails too, the facts are shown as a table: no words, no risk." },
  ],
};

export function QuestionChart({ live }: { live: boolean }) {
  return (
    <Chart
      title="A question goes planner, fetch, writer, grounding check, reply. A failed check retries once without tools; a second miss shows a plain table instead."
      layout={Q_LAYOUT}
      edges={Q_EDGES}
      active={[0, 1, 2, 3]}
      live={live}
    />
  );
}

/* ---------------------------------------------------------------- the learning loop */

const L_EDGES: CEdge[] = [
  { from: "suggest", to: "you" },
  { from: "you", to: "post", label: "1 signal a day" },
  { from: "post", to: "rank" },
  { from: "rank", to: "suggest", label: "next week" },
];

/** Beta curves, drawn at the posteriors box: wide after one signal, sharp after many. */
function BetaCurves({ x, y }: { x: number; y: number }) {
  const curve = (a: number, b: number) => {
    const pts: string[] = [];
    let max = 0;
    const ys: number[] = [];
    for (let i = 0; i <= 40; i++) {
      const p = 0.01 + (0.98 * i) / 40;
      const v = p ** (a - 1) * (1 - p) ** (b - 1);
      ys.push(v);
      max = Math.max(max, v);
    }
    ys.forEach((v, i) => pts.push(`${x + (i / 40) * 150},${y - (v / max) * 34}`));
    return `M${pts.join(" L")}`;
  };
  return (
    <g className="ar-beta" aria-hidden="true">
      <path d={curve(2, 2)} style={{ "--b": 0 } as React.CSSProperties} />
      <path d={curve(5, 3)} style={{ "--b": 1 } as React.CSSProperties} />
      <path d={curve(16, 7)} style={{ "--b": 2 } as React.CSSProperties} />
    </g>
  );
}

const L_LAYOUT: Layout = {
  vb: [440, 300],
  nodes: [
    { id: "suggest", x: 4, y: 8, w: 170, label: "Suggestion", sub: "Insight or card", tip: "At most 3 a week, chosen by one seeded draw per arm." },
    { id: "you", x: 266, y: 8, w: 170, label: "You", sub: "accept, thumbs, dismiss", tip: "Acceptances, thumbs and dismissals are the only rewards: never clicks or time." },
    { id: "post", x: 266, y: 150, w: 170, h: 126, label: "Posteriors", sub: "Beta per cell", tip: "Beta(1, 1) to start; each signal sharpens one cell's curve.", solid: true },
    { id: "rank", x: 4, y: 150, w: 170, label: "Ranking", sub: "impact × confidence", tip: "Relevance multiplies impact and confidence; a constraint Memory can rule one out." },
  ],
};

export function LearningChart({ live }: { live: boolean }) {
  return (
    <div className="ar-learn">
      <Chart
        title="The learning loop: a suggestion, your response, the posteriors update, the ranking changes, and next week's suggestion follows."
        layout={L_LAYOUT}
        edges={L_EDGES}
        active={[0, 1, 2, 3]}
        live={live}
        extra={<BetaCurves x={276} y={262} />}
      />
      <Reveal className="ar-pair-q">
        <p>Which would you cut first?</p>
        <div>
          <span data-pick>Eating out</span>
          <span>Subscriptions</span>
        </div>
        <p className="ar-pair-note">One answer moves two strengths: P(a beats b) = σ(sₐ − s_b).</p>
      </Reveal>
    </div>
  );
}

/* ---------------------------------------------------------------- evaluations and the gate */

const E_EDGES: CEdge[] = [
  { from: "golden", to: "bench" },
  { from: "bench", to: "results" },
  { from: "results", to: "gate" },
  { from: "gate", to: "ci" },
  { from: "ci", to: "main" },
];

const E_LAYOUT: Layout = {
  vb: [440, 180],
  nodes: [
    { id: "golden", x: 4, y: 8, w: 128, label: "Golden set", sub: "synthetic", tip: "Questions with the expected route, tool calls and figures." },
    { id: "bench", x: 156, y: 8, w: 128, label: "Benchmark", sub: "per model", tip: "Routing, tools, grounding, refusals, latency and peak memory on the M1 Pro." },
    { id: "results", x: 308, y: 8, w: 128, label: "Results", sub: "committed", tip: "The scores live in the repo, next to the code they describe." },
    { id: "gate", x: 308, y: 116, w: 128, label: "Results gate", sub: "fresh = committed", tip: "Fails unless a fresh run reproduces the committed results, and refuses a lower metric." },
    { id: "ci", x: 156, y: 116, w: 128, label: "CI checks", sub: "5 parallel jobs", tip: "Lint, Python tests, evals, web and WebKit smoke, under one aggregate check." },
    { id: "main", x: 4, y: 116, w: 128, label: "main", sub: "protected", tip: "Nothing merges on a red job.", solid: true },
  ],
};

export function EvalChart({ live }: { live: boolean }) {
  return (
    <Chart
      title="Evaluations: a synthetic golden set is benchmarked per model, results are committed, a gate checks a fresh run reproduces them, and CI's aggregate check guards main."
      layout={E_LAYOUT}
      edges={E_EDGES}
      active={[0, 1, 2, 3, 4]}
      live={live}
    />
  );
}

/* ---------------------------------------------------------------- small pictures */

/** Reveals its children once, the first time they reach the screen. */
export function Reveal({ children, className }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const seen = useInView(ref, { once: true, amount: 0.4 });
  return (
    <div ref={ref} className={`ar-reveal ${className ?? ""}`} data-in={seen || undefined}>
      {children}
    </div>
  );
}

/** The four tiers, widest at the bottom: a question falls until a tier holds the answer. */
export function Tiers() {
  const rows = [
    ["L0 Profile", "always in the prompt, < 800 tokens"],
    ["L1 Summaries", "pre-computed, milliseconds"],
    ["L2 Meaning index", "FTS5 and vectors, fused"],
    ["L3 Ledger", "SQL, every row, exact"],
  ];
  return (
    <Reveal className="ar-tiers">
      <ol aria-label="The four tiers, smallest first">
        {rows.map(([a, b], i) => (
          <li key={a} style={{ "--i": i } as React.CSSProperties}>
            <strong>{a}</strong>
            <span>{b}</span>
          </li>
        ))}
      </ol>
      <p className="ar-figcap">A question falls through until a tier holds its answer; totals always come from the Ledger.</p>
    </Reveal>
  );
}

/** The prompt as a stack: the cached prefix holds still while the turn's part is rebuilt. */
export function PromptStack() {
  const rows: [string, boolean][] = [
    ["System prefix", true],
    ["L0 profile", true],
    ["Memories", true],
    ["Summary of older turns", false],
    ["Last 3 turns", false],
    ["This turn's facts", false],
    ["Question", false],
  ];
  return (
    <Reveal className="ar-stack">
      <div className="ar-stack-rows">
        <span className="ar-stack-band" aria-hidden="true">
          KV cache
        </span>
        <ol aria-label="The prompt's order">
          {rows.map(([label, cached], i) => (
            <li key={label} data-cached={cached || undefined} style={{ "--i": i } as React.CSSProperties}>
              {label}
            </li>
          ))}
        </ol>
      </div>
      <p className="ar-figcap">The prefix is reused from the cache; only the rows below it are processed each turn.</p>
    </Reveal>
  );
}

/** A small picture beside each security decision. */
export const SECURITY_VISUAL: Record<string, () => ReactNode> = {
  egress: () => (
    <div className="ar-sv ar-sv-hosts">
      <span data-ok>localhost:11434</span>
      <span data-ok>SimpleFIN</span>
      <span data-ok>Gemini</span>
      <span data-no>anything else</span>
    </div>
  ),
  keychain: () => (
    <div className="ar-sv ar-sv-keys">
      {["Database key", "Account HMAC key", "SimpleFIN token", "Gemini key"].map((k) => (
        <span key={k}>{k}</span>
      ))}
      <strong>macOS Keychain</strong>
    </div>
  ),
  sqlcipher: () => (
    <div className="ar-sv ar-sv-db">
      <svg viewBox="0 0 60 70" aria-hidden="true">
        <ellipse cx="30" cy="12" rx="26" ry="9" />
        <path d="M4 12v44c0 5 12 9 26 9s26-4 26-9V12" />
        <path d="M4 34c0 5 12 9 26 9s26-4 26-9" />
      </svg>
      <span>Ledger, graph checkpoints and answer cache: one encrypted file</span>
    </div>
  ),
  placeholders: () => (
    <div className="ar-sv ar-sv-swap">
      <span>£64 at Corner Café on 3 Nov</span>
      <span aria-hidden="true">↓</span>
      <code>{"{P1} at {M1} on {D1}"}</code>
    </div>
  ),
  injection: () => (
    <div className="ar-sv ar-sv-swap">
      <span>
        CARD PAYMENT <s>ignore previous instructions</s>
      </span>
      <span aria-hidden="true">↓</span>
      <code>"CARD PAYMENT"</code>
    </div>
  ),
  nopay: () => (
    <div className="ar-sv ar-sv-hosts">
      <span data-no>Send money</span>
      <span data-no>Transfer</span>
      <span data-ok>Read balances</span>
    </div>
  ),
  proposals: () => (
    <div className="ar-sv ar-sv-card">
      <p>Recategorise 4 payments to Groceries?</p>
      <div>
        <span data-pick>Confirm</span>
        <span>Dismiss</span>
      </div>
    </div>
  ),
};
