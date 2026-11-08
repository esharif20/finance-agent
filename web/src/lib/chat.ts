/** Chat state for the Assistant panel (#145): SSE parsing, the turn reducer, reply blocks.
 * Model text is only ever data here; components render it as text nodes, never HTML. */

export type Path = "cache" | "local_model" | "deterministic";
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

export type Message = {
  id: number;
  role: "user" | "assistant";
  text: string;
  path?: Path;
  proposalId?: number | null;
  streaming?: boolean;
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
        return last({ ...s, thread }, (m) => ({ ...m, path: data.path as Path, proposalId }));
      }
      if (name === "delta" && typeof data.text === "string") {
        const text = data.text;
        return last(s, (m) => ({ ...m, text: m.text + text }));
      }
      if (name === "done") return { ...last(s, (m) => ({ ...m, streaming: false })), busy: false };
      return s;
    }
  }
}

export function pathLabel(path: Path, reply: string): string {
  if (path === "cache") return "Cache";
  if (path === "local_model") return "Local model";
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
