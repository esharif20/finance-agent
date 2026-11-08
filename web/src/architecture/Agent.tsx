/* "The agent": the real LangGraph graph in src/finance_agent/assistant/graph.py (router, four
   route sub-graphs, finish, confirm), its routes and loop bounds, the grounding retry and table,
   the interrupt for a Proposal, and the checkpoint it resumes from. Planned parts sit apart,
   dashed, with a Planned pill. */
import Chart, { type CEdge, type CNode, type Layout } from "./Chart";
import { REPO } from "./nodes";

const TIP: Record<string, string> = {
  message: "One message on a thread. The checkpointed state holds the turn, never tool results.",
  router: "A Plan from code first, then keywords; only then one model call that answers in one word.",
  zero: "A figure question becomes a Plan and a template: the Engine computes it, no model call.",
  one: "Open or in depth: up to 3 Engine reads in parallel, then one writer call offered no tools.",
  loop: "Memory, Correct and the rest: allowlisted tools only, at most 3 rounds of 4 calls.",
  check: "Every £ figure, percentage and date must match a tool value from this turn.",
  regen: "A reply that fails the check is regenerated once.",
  table: "If it fails again, the facts are shown as a deterministic table.",
  finish: "Writes the turn into the thread's history and topics.",
  confirm: "A write is a Proposal: interrupt() pauses the graph until you confirm or reject it.",
  saver: "LedgerSaver keeps the paused thread in the encrypted database; Command(resume) continues it.",
  prouter: "Planned, #284 and #285: a learned router choosing the model for each question.",
  pbandit: "Planned, #272 runs in shadow today: follow-up suggestions ranked by the bandit.",
};

const node = (id: string, x: number, y: number, w: number, label: string, sub: string, more: Partial<CNode> = {}): CNode => ({
  id,
  x,
  y,
  w,
  label,
  sub,
  tip: TIP[id] ?? "",
  ...more,
});

const EDGES: CEdge[] = [
  { from: "message", to: "router" },
  { from: "router", to: "zero", label: "figure" },
  { from: "router", to: "one", label: "open, in depth" },
  { from: "router", to: "loop", label: "the rest" },
  { from: "zero", to: "check", via: "h" },
  { from: "one", to: "check" },
  { from: "loop", to: "check", via: "h" },
  { from: "check", to: "finish", label: "pass" },
  { from: "check", to: "regen", label: "miss" },
  { from: "regen", to: "table", label: "miss" },
  { from: "finish", to: "confirm", label: "a Proposal?" },
  { from: "confirm", to: "saver", dashed: true },
];

const WIDE: Layout = {
  vb: [1100, 520],
  zones: [{ x: 836, y: 326, w: 258, h: 188, label: "Orchestration", planned: true }],
  nodes: [
    node("message", 6, 210, 116, "Message", "on a thread"),
    node("router", 172, 210, 130, "router", "3 ways to route", { solid: true }),
    node("zero", 410, 70, 176, "0 model calls", "template from a Plan"),
    node("one", 410, 210, 176, "1 writer call", "parallel fetch first"),
    node("loop", 410, 350, 176, "Tool loop", "≤ 3 rounds × 4 calls"),
    node("check", 660, 210, 150, "Grounding check", "every figure"),
    node("regen", 660, 350, 150, "Regenerate", "once"),
    node("table", 660, 452, 150, "Table", "deterministic"),
    node("finish", 870, 210, 110, "finish", "history"),
    node("confirm", 870, 110, 110, "confirm", "interrupt()", { solid: true }),
    node("saver", 870, 8, 110, "Checkpoint", "pause, resume"),
    node("prouter", 852, 372, 226, "Learned model router", "#284, #285", { planned: true }),
    node("pbandit", 852, 440, 226, "Bandit follow-ups", "#272, in shadow", { planned: true }),
  ],
};

const TALL: Layout = {
  vb: [400, 1010],
  zones: [{ x: 6, y: 830, w: 388, h: 174, label: "Orchestration", planned: true }],
  nodes: [
    node("message", 130, 6, 140, "Message", "on a thread"),
    node("router", 130, 104, 140, "router", "3 ways", { solid: true }),
    node("zero", 6, 214, 124, "0 calls", "template"),
    node("one", 138, 214, 124, "1 call", "fetch, write"),
    node("loop", 270, 214, 124, "Tool loop", "≤ 3 × 4"),
    node("check", 130, 324, 140, "Grounding", "every figure"),
    node("regen", 6, 430, 140, "Regenerate", "once"),
    node("table", 6, 530, 140, "Table", "deterministic"),
    node("finish", 250, 430, 144, "finish", "history"),
    node("confirm", 250, 530, 144, "confirm", "interrupt()", { solid: true }),
    node("saver", 250, 640, 144, "Checkpoint", "encrypted"),
    node("prouter", 26, 872, 348, "Learned model router", "#284, #285", { planned: true }),
    node("pbandit", 26, 938, 348, "Bandit follow-ups", "#272, in shadow", { planned: true }),
  ],
};

/** the bounded tool loop: a small arc back into its own box */
const loopArc = (l: Layout): CEdge => {
  const b = l.nodes.find((n) => n.id === "loop");
  if (!b) return { from: "loop", to: "loop" };
  const r = b.x + b.w;
  const y = b.y + 54;
  return { from: "loop", to: "loop", d: `M${r - 30} ${y} C${r - 30} ${y + 34} ${r + 18} ${y + 30} ${r + 4} ${y - 8}` };
};

const STEPS: [string, string][] = [
  ["Plan", "Code reads the question against your own data and picks a route, before any model call."],
  ["Act", "Read-only Engine tools from an allowlist per route, in a bounded loop or one parallel round."],
  ["Check", "Every figure in the draft must match a tool value from this turn."],
  ["Retry", "One regeneration on a miss, then a plain table rather than an unchecked sentence."],
  ["Wait for you", "Any write pauses the graph as a Proposal until you confirm it; the thread is checkpointed meanwhile."],
];

const RAILS = ["Tool allowlist per route", "No payment or transfer tool exists", "Figures only from tool results"];

export default function Agent({ wide, live }: { wide: boolean; live: boolean }) {
  const layout = wide ? WIDE : TALL;
  return (
    <section className="ar-chapter ar-agent" id="ar-agent" aria-labelledby="ar-h-agent">
      <div className="ar-chapter-text">
        <h2 id="ar-h-agent">The agent</h2>
        <p className="ar-lede">
          One LangGraph state graph: a router, four route sub-graphs, then finish and confirm. It plans, acts with
          tools, checks itself, retries once, and stops to ask before it changes anything.
        </p>
      </div>
      <Chart
        className="ar-chart-agent"
        title="The Assistant's graph: a message goes to the router, which picks a template with no model call, one writer call after a parallel fetch, or a bounded tool loop. Every reply passes the grounding check: on a miss it regenerates once, then shows a table. Finish records the turn; confirm pauses on a Proposal until you answer, with the thread checkpointed in the encrypted database. Planned, not built: a learned model router and bandit-ranked follow-ups."
        layout={layout}
        edges={[...EDGES, loopArc(layout)]}
        active={[0, 2, 5, 7, 10]}
        live={live}
      />
      <div className="ar-agent-low">
        <ol className="ar-loop" aria-label="The agent loop">
          {STEPS.map(([a, b]) => (
            <li key={a}>
              <strong>{a}</strong>
              <span>{b}</span>
            </li>
          ))}
        </ol>
        <div className="ar-rails">
          <h3>Guardrails</h3>
          <ul>
            {RAILS.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
          <p>
            From <a href={`${REPO}/blob/main/src/finance_agent/assistant/graph.py`}>assistant/graph.py</a>,{" "}
            <a href={`${REPO}/blob/main/docs/adr/0008-langgraph-single-graph.md`}>ADR 0008</a> and{" "}
            <a href={`${REPO}/blob/main/docs/adr/0007-engine-computes-assistant-explains.md`}>ADR 0007</a>.
          </p>
        </div>
      </div>
    </section>
  );
}
