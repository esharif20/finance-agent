/** Chat state for the Assistant panel (#145): SSE parsing, the turn reducer, reply blocks.
 * Model text is only ever data here; components render it as text nodes, never HTML. */

export type Path = "cache" | "local_model" | "cloud" | "deterministic" | "planner" | "miss";
export type SseEvent = { name: string; data: Record<string, unknown> };

export function parseSse(buffer: string, chunk: string): { events: SseEvent[]; rest: string } {
  const parts = (buffer + chunk).replace(/\r\n/g, "\n").split("\n\n");
  const rest = parts.pop() ?? "";
  const events: SseEvent[] = [];
  for (const part of parts) {
    let name = "message";
    const data: string[] = [];
    for (const line of part.split("\n")) {
      if (line.startsWith("event:")) name = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    try {
      const parsed: unknown = JSON.parse(data.join("\n"));
      if (parsed && typeof parsed === "object") events.push({ name, data: parsed as Record<string, unknown> });
    } catch {
      /* a malformed event is dropped */
    }
  }
  return { events, rest };
}

/** A Transaction a spending total counted, from the API (ADR 0007): never the model's. */
export type CountedTxn = { id: number; posted_on: string; merchant: string; amount_minor: number };
export type Counted = { evidence_id: string; total_minor: number; transactions: CountedTxn[] };

/** Server-measured time for one turn (#317): monotonic ms per phase, never content. */
export type Timing = { total_ms: number; phases: Record<string, number>; model_calls: number; model: string | null };

export type Message = {
  id: number;
  role: "user" | "assistant";
  text: string;
  path?: Path;
  costPence?: number | null;
  proposalId?: number | null;
  streaming?: boolean;
  stage?: string; // progress words while the turn runs
  counted?: Counted[];
  chips?: string[]; // follow-ups the server can answer, sent as typed when tapped
  resolved?: string[]; // how the question was read, e.g. "Transport › Fuel"
  timing?: Timing;
  waitedMs?: number; // send to done, measured in the browser
  error?: string;
};
export type Decision = "confirm" | "reject";
export type Chat = {
  thread: string | null;
  messages: Message[];
  busy: boolean;
  decisions: Record<number, Decision>;
  next: number;
};
export const initial: Chat = { thread: null, messages: [], busy: false, decisions: {}, next: 1 };

export type Action =
  | { type: "send"; text: string }
  | { type: "decide"; proposalId: number; decision: Decision }
  | { type: "event"; event: SseEvent }
  | { type: "fail"; error: string }
  | { type: "reset" };

const start = (s: Chat, user?: string): Chat => {
  const messages = [...s.messages];
  let id = s.next;
  if (user !== undefined) messages.push({ id: id++, role: "user", text: user });
  messages.push({ id: id++, role: "assistant", text: "", streaming: true });
  return { ...s, messages, busy: true, next: id };
};
const last = (s: Chat, f: (m: Message) => Message): Chat => ({
  ...s,
  messages: s.messages.map((m, i) => (i === s.messages.length - 1 ? f(m) : m)),
});

export function reduce(s: Chat, a: Action): Chat {
  switch (a.type) {
    case "send":
      return start(s, a.text);
    case "decide":
      return start({ ...s, decisions: { ...s.decisions, [a.proposalId]: a.decision } });
    case "fail":
      return { ...last(s, (m) => ({ ...m, streaming: false, error: a.error })), busy: false };
    case "reset":
      return initial;
    case "event": {
      const { name, data } = a.event;
      if (name === "meta") {
        const thread = typeof data.thread_id === "string" ? data.thread_id : s.thread;
        const proposalId = typeof data.proposal_id === "number" ? data.proposal_id : null;
        const counted = parseCounted(data.counted);
        const extra = { chips: strings(data.chips), resolved: strings(data.resolved), timing: parseTiming(data.timing) };
        return last({ ...s, thread }, (m) => ({ ...m, path: data.path as Path, proposalId, counted, ...extra, costPence: typeof data.cost_pence === "number" ? data.cost_pence : null, stage: undefined }));
      }
      if (name === "stage" && typeof data.text === "string") {
        const stage = data.text;
        return last(s, (m) => ({ ...m, stage }));
      }
      if (name === "delta" && typeof data.text === "string") {
        const text = data.text;
        return last(s, (m) => ({ ...m, text: m.text + text }));
      }
      if (name === "done") {
        const waited = typeof data.waited_ms === "number" ? { waitedMs: data.waited_ms } : {};
        return { ...last(s, (m) => ({ ...m, ...waited, streaming: false })), busy: false };
      }
      return s;
    }
  }
}

const isInt = (v: unknown): v is number => Number.isSafeInteger(v);
const strings = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string").slice(0, 4) : []);
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0;

export function parseTiming(raw: unknown): Timing | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const t = raw as Record<string, unknown>;
  if (!finite(t.total_ms) || !isInt(t.model_calls)) return undefined;
  const phases = Object.fromEntries(
    Object.entries(t.phases && typeof t.phases === "object" ? t.phases : {}).filter(([, v]) => finite(v)),
  ) as Record<string, number>;
  return { total_ms: t.total_ms, phases, model_calls: t.model_calls, model: typeof t.model === "string" && t.model ? t.model : null };
}

const secs = (ms: number) => (ms < 100 ? "0.1 s" : `${(ms / 1000).toFixed(1)} s`);
/** "qwen3.5:4b-q4_K_M" -> "qwen 4b": the family and size, readable at a glance. */
export function shortModel(name: string): string {
  const m = /^([a-z]+)[\d.]*:?(\d+(?:\.\d+)?b)?/i.exec(name);
  return m ? [m[1], m[2]].filter(Boolean).join(" ") : name;
}

/** "1.2 s · 0 model calls" or "18.4 s · 1 call · qwen 4b": the wait the user saw. */
export function timingLine(t: Timing, waitedMs?: number): string {
  const parts = [secs(Math.max(waitedMs ?? 0, t.total_ms))];
  if (t.model_calls === 0) parts.push("0 model calls");
  else parts.push(t.model_calls === 1 ? "1 call" : `${t.model_calls} calls`);
  if (t.model_calls && t.model) parts.push(shortModel(t.model));
  return parts.join(" · ");
}

const PHASE: Record<string, string> = { plan: "Reading the question", fetch: "Fetching figures", model: "Model", check: "Checking figures", other: "Other" };
/** The breakdown rows, in pipeline order, plus the network share the browser saw. */
export function timingRows(t: Timing, waitedMs?: number): [string, string][] {
  const known = Object.keys(PHASE).filter((k) => k in t.phases);
  const rest = Object.keys(t.phases).filter((k) => !(k in PHASE));
  const rows: [string, string][] = [...known, ...rest].map((k) => [PHASE[k] ?? k, secs(t.phases[k] ?? 0)]);
  if (waitedMs !== undefined && waitedMs > t.total_ms) rows.push(["Network", secs(waitedMs - t.total_ms)]);
  return rows;
}

/** The meta event's `counted`, keeping only well-formed groups. */
export function parseCounted(raw: unknown): Counted[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((g: Record<string, unknown> | null) => {
    if (!g || typeof g.evidence_id !== "string" || !isInt(g.total_minor) || !Array.isArray(g.transactions)) return [];
    const transactions = (g.transactions as (Record<string, unknown> | null)[]).filter(
      (t): t is CountedTxn =>
        !!t && isInt(t.id) && isInt(t.amount_minor) && typeof t.posted_on === "string" && typeof t.merchant === "string",
    );
    return transactions.length ? [{ evidence_id: g.evidence_id, total_minor: g.total_minor, transactions }] : [];
  });
}

/** Integer minor units, so the listed rows add up exactly (never floats). */
export const sumMinor = (rows: CountedTxn[]) => rows.reduce((n, t) => n + t.amount_minor, 0);

export function pathLabel(path: Path, reply: string): string {
  if (path === "cache") return "Cache";
  if (path === "local_model") return "Local model";
  if (path === "cloud") return "Cloud model";
  if (path === "planner") return "Built in";
  if (path === "miss") return "Not answered";
  return parseBlocks(reply).some((b) => b.kind === "table") ? "Table" : "Built in";
}

export type Block = { kind: "text"; text: string } | { kind: "table"; head: string[]; rows: string[][] };

const cells = (line: string) =>
  line
    .trim()
    .replace(/^\||\|$/g, "")
    .split(/(?<!\\)\|/)
    .map((c) => c.trim().replace(/\\\|/g, "|"));
const isRow = (l: string) => /^\s*\|.*\|\s*$/.test(l);
const isRule = (l: string) => /^\s*\|(\s*:?-{3,}:?\s*\|)+\s*$/.test(l);

/** Plain text plus the Markdown tables the deterministic fallback writes. Nothing else is parsed. */
export function parseBlocks(reply: string): Block[] {
  const lines = reply.split("\n");
  const out: Block[] = [];
  let text: string[] = [];
  const flush = () => {
    const t = text.join("\n").trim();
    if (t) out.push({ kind: "text", text: t });
    text = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] ?? "";
    if (isRow(line) && isRule(lines[i + 1] ?? "")) {
      flush();
      const rows: string[][] = [];
      let j = i + 2;
      while (j < lines.length && isRow(lines[j] ?? "")) rows.push(cells(lines[j++] ?? ""));
      out.push({ kind: "table", head: cells(line), rows });
      i = j - 1;
    } else text.push(line);
  }
  flush();
  return out;
}

const FIELDS: Record<string, string> = {
  month: "Month",
  money_in: "Money in",
  money_out: "Money out",
  net: "Net",
  spending: "Spending",
  refunds: "Refunds",
  fees: "Fees",
  unmatched_refunds: "Unmatched refunds",
  posted_on: "Posted",
  amount: "Amount",
  outflow: "Outflow",
};
const TOOLS = /^(spending_summary|find_transactions)\s+(.*)$/;
const PERIOD = /^(\d{4})-(0[1-9]|1[0-2])(?:-(0[1-9]|[12]\d|3[01]))?\s+(.*)$/;

/** A tool value's raw label ("spending_summary 2026-03 money_in") in plain words. Anything
 * it does not recognise comes back unchanged, so a new tool field still shows its raw key. */
export function friendlyLabel(raw: string): string {
  const tool = TOOLS.exec(raw);
  if (!tool) return raw;
  const [, name, rest = ""] = tool;
  if (name === "find_transactions" && rest === "total") return "Transactions found";
  const p = PERIOD.exec(name === "find_transactions" ? rest.replace(/^rows\s+/, "") : rest);
  if (!p) return raw;
  const [, y, m, d, field = ""] = p;
  const when = new Date(Date.UTC(Number(y), Number(m) - 1, Number(d ?? 1))).toLocaleDateString("en-GB", {
    timeZone: "UTC",
    ...(d ? { day: "numeric" } : {}),
    month: "long",
    year: "numeric",
  });
  const cat = /^categories\s+(.+)\s+amount$/.exec(field);
  const what = cat ? cat[1] : FIELDS[field];
  return what ? `${when} · ${what}` : raw;
}
