import { useQuery } from "@tanstack/react-query";
import { motion, useReducedMotion } from "motion/react";
import { ArrowUp, Check, ChevronDown, MessageCircle, X } from "lucide-react";
import { Fragment, useEffect, useRef, useState } from "react";
import type { components } from "@/api/schema";
import { CloudToggle } from "@/components/CloudToggle";
import { Money } from "@/components/Money";
import { PayloadViewer } from "@/components/PayloadViewer";
import { Card } from "@/components/ui/card";
import { type Block, type Chart, type Counted, type Decision, type Message, friendlyLabel, parseBlocks, pathLabel, timingLine, timingRows } from "@/lib/chat";
import { decide, send as sendMessage, useChat } from "@/lib/chatStore";
import { usePrivacy } from "@/lib/privacy";

type Proposal = components["schemas"]["ProposalOut"];

const FIGURE = /([−-]?[£$€]\s?\d[\d,]*(?:\.\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+)/;

/** Text with every figure in a `.money` span, so Hide amounts blurs it. Plain text nodes only. */
export function Figures({ text }: { text: string }) {
  const { hidden } = usePrivacy();
  return (
    <>
      {text.split(FIGURE).map((part, i) =>
        i % 2 ? (
          <span key={i} className="money whitespace-nowrap">
            {hidden && <span className="sr-only">Amount hidden</span>}
            <span aria-hidden={hidden || undefined}>{part}</span>
          </span>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </>
  );
}

function ReplyTable({ block }: { block: Extract<Block, { kind: "table" }> }) {
  return (
    <div className="my-2 max-h-96 overflow-auto rounded-xl border border-border">
      <table className="w-full text-label">
        <caption className="sr-only">Figures behind the answer</caption>
        <thead>
          <tr className="text-left text-muted-foreground">
            {block.head.map((h, i) => (
              <th key={i} scope="col" className={`px-3 py-2 font-semibold ${i === 1 ? "text-right" : ""}`}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {block.rows.map((row, r) => (
            <tr key={r} className="border-t border-border align-top">
              {row.map((c, i) => (
                <td key={i} className={`px-3 py-2 tabular-nums ${i === 1 ? "text-right font-medium" : ""} ${i === 2 ? "break-all text-muted-foreground" : ""}`}>
                  {i === 0 ? friendlyLabel(c) : <Figures text={c} />}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Reply({ text }: { text: string }) {
  return (
    <>
      {parseBlocks(text).map((b, i) =>
        b.kind === "table" ? (
          <ReplyTable key={i} block={b} />
        ) : (
          <p key={i} className="whitespace-pre-wrap text-row">
            <Figures text={b.text} />
          </p>
        ),
      )}
    </>
  );
}

const day = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", { timeZone: "UTC", day: "numeric", month: "short" });

/** The Transactions a figure counted, straight from the API's tool result (ADR 0007).
 * They sum to the total exactly: the server builds both from the same shares. */
function CountedList({ group }: { group: Counted }) {
  const n = group.transactions.length;
  return (
    <details className="group mt-3 rounded-2xl bg-raised" data-testid="counted">
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 rounded-2xl px-4 text-label font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
        Show transactions ({n})
        <ChevronDown className="size-4 shrink-0 transition-transform duration-200 ease-out group-open:rotate-180 motion-reduce:transition-none" aria-hidden="true" />
      </summary>
      <ul className="max-h-80 overflow-auto px-4 pb-1 text-label" aria-label="Transactions counted">
        {group.transactions.map((t) => (
          <li key={t.id} className="flex items-baseline gap-3 border-t border-border py-2 first:border-t-0">
            <span className="w-14 shrink-0 tabular-nums text-muted-foreground">{day(t.posted_on)}</span>
            <span className="min-w-0 flex-1 truncate">{t.merchant}</span>
            <span className="tabular-nums font-medium" data-minor={t.amount_minor}>
              <Money value={{ minor: t.amount_minor, currency: "GBP" }} />
            </span>
          </li>
        ))}
      </ul>
      <p className="flex justify-between gap-3 border-t border-border px-4 py-2 text-label font-semibold">
        <span>Total</span>
        <span className="tabular-nums">
          <Money value={{ minor: group.total_minor, currency: "GBP" }} />
        </span>
      </p>
    </details>
  );
}

/** Where a figure went, as bars (#317). Static: it draws once. Amounts are `Money`, so
 * Hide amounts blurs them; the bars' widths are shares of the largest. */
function ReplyChart({ chart }: { chart: Chart }) {
  const top = Math.max(...chart.bars.map((b) => Math.abs(b.minor)), 1);
  return (
    <figure className="mt-3 rounded-2xl bg-raised px-4 py-3" data-testid="chart">
      <figcaption className="text-label font-semibold">{chart.title}</figcaption>
      <ul className="mt-2 space-y-2 text-label" aria-label={`${chart.title}, by merchant`}>
        {chart.bars.map((b, i) => (
          <li key={i} className="grid grid-cols-[minmax(0,7rem)_1fr_auto] items-center gap-3">
            <span className="truncate">{b.label}</span>
            <span className="h-2 rounded-full bg-active" aria-hidden="true">
              <span className="block h-2 rounded-full bg-primary" style={{ width: `${(Math.abs(b.minor) / top) * 100}%` }} />
            </span>
            <span className="tabular-nums font-medium" data-minor={b.minor}>
              <Money value={{ minor: b.minor, currency: chart.currency }} />
            </span>
          </li>
        ))}
      </ul>
    </figure>
  );
}

/** How long the user waited and what ran (#317); tap for the per-phase breakdown. */
function TimingLine({ m }: { m: Message }) {
  const label = m.path ? `Answered by: ${pathLabel(m.path, m.text)}` : null;
  if (!m.timing) return label ? <p className="mt-2 text-meta text-muted-foreground" data-testid="path">{label}</p> : null;
  return (
    <details className="group mt-2 text-meta text-muted-foreground" data-testid="timing">
      <summary className="inline-flex min-h-8 cursor-pointer list-none items-center gap-1 rounded-full tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
        {timingLine(m.timing, m.waitedMs)}
        <ChevronDown className="size-3.5 transition-transform duration-200 ease-out group-open:rotate-180 motion-reduce:transition-none" aria-hidden="true" />
      </summary>
      <dl className="mt-1 grid max-w-64 grid-cols-[1fr_auto] gap-x-4 gap-y-0.5 tabular-nums">
        {timingRows(m.timing, m.waitedMs).map(([k, v]) => (
          <Fragment key={k}>
            <dt>{k}</dt>
            <dd className="text-right">{v}</dd>
          </Fragment>
        ))}
      </dl>
      {label && <p className="mt-1" data-testid="path">{label}</p>}
    </details>
  );
}

function ProposalCard({
  id,
  decision,
  disabled,
  onDecide,
}: {
  id: number;
  decision?: Decision;
  disabled: boolean;
  onDecide: (d: Decision) => void;
}) {
  const { data, isError } = useQuery({
    queryKey: ["proposal", id],
    queryFn: async () => {
      const res = await fetch(`/api/proposals/${id}`);
      if (!res.ok) throw new Error(String(res.status));
      return (await res.json()) as Proposal;
    },
  });
  const m = data?.memory;
  const c = data?.correction;
  const settled = decision ?? (data && data.status !== "pending" ? (data.status === "confirmed" ? "confirm" : "reject") : undefined);
  return (
    <section aria-label="Proposed change" className="mt-3 rounded-2xl border border-border bg-raised p-4">
      <h3 className="text-label font-semibold">{m ? "Remember this?" : "Change this?"}</h3>
      {isError && <p className="mt-2 text-label text-muted-foreground">This proposal could not be loaded.</p>}
      {m && (
        <>
          <p className="mt-2 text-row">{m.text}</p>
          <p className="mt-1 text-meta text-muted-foreground">You said: “{m.quote}”</p>
        </>
      )}
      {c && (
        <div className="mt-2 text-row">
          <p>
            Transaction {c.transaction_id}: {c.kind === "note" ? `note “${c.note}”` : c.kind}
            {c.category ? ` → ${c.category.join(" › ")}` : ""}
          </p>
          {c.parts && (
            <ul className="mt-1 space-y-0.5 text-label">
              {c.parts.map((p, i) => (
                <li key={i} className="flex justify-between gap-3">
                  <span>{p.category.join(" › ")}</span>
                  <Money value={{ minor: p.amount_minor, currency: "GBP" }} />
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {settled ? (
        <p className="mt-3 inline-flex items-center gap-1.5 text-label font-semibold" role="status">
          {settled === "confirm" ? <Check className="size-4" aria-hidden="true" /> : <X className="size-4" aria-hidden="true" />}
          {settled === "confirm" ? "Confirmed" : "Rejected"}
        </p>
      ) : (
        <div className="mt-3 flex gap-2">
          <button type="button" disabled={disabled || !data} onClick={() => onDecide("confirm")} className="min-h-11 rounded-full bg-primary px-5 text-sm font-semibold text-primary-foreground transition-transform active:scale-[0.97] disabled:opacity-50">
            Confirm
          </button>
          <button type="button" disabled={disabled} onClick={() => onDecide("reject")} className="min-h-11 rounded-full bg-active px-5 text-sm font-semibold transition-transform active:scale-[0.97] disabled:opacity-50">
            Reject
          </button>
        </div>
      )}
    </section>
  );
}

function Bubble({ m, decision, busy, onDecide, onChip, thread, cloudIndex }: { m: Message; decision?: Decision; busy: boolean; onDecide: (id: number, d: Decision) => void; onChip: (text: string) => void; thread: string | null; cloudIndex: number }) {
  const reduce = useReducedMotion();
  const mine = m.role === "user";
  return (
    <motion.li
      initial={reduce ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18, ease: [0.23, 1, 0.32, 1] }}
      className={`flex ${mine ? "justify-end" : "justify-start"}`}
    >
      <div className={`max-w-[88%] rounded-3xl px-4 py-3 ${mine ? "bg-primary text-primary-foreground" : "bg-card"}`}>
        {mine ? (
          <p className="whitespace-pre-wrap text-row">{m.text}</p>
        ) : (
          <>
            {m.text ? (
              <Reply text={m.text} />
            ) : m.error ? null : (
              <span className="text-label text-muted-foreground" data-testid="stage">
                {m.stage ?? "Reading your question…"}
              </span>
            )}
            {m.error && <p className="text-label text-warning-foreground" role="alert">{m.error}</p>}
            {!m.streaming && m.chart && <ReplyChart chart={m.chart} />}
            {!m.streaming && !!m.resolved?.length && (
              <p className="mt-1 text-meta text-muted-foreground" data-testid="resolved">
                Read as {m.resolved.join(", ")}
              </p>
            )}
            {!m.streaming && m.counted?.map((g) => <CountedList key={g.evidence_id} group={g} />)}
            {m.proposalId != null && !m.streaming && (
              <ProposalCard id={m.proposalId} decision={decision} disabled={busy} onDecide={(d) => onDecide(m.proposalId as number, d)} />
            )}
            {m.path === "cloud" && !m.streaming && thread && <PayloadViewer conversation={thread} index={cloudIndex} cost={m.costPence} />}
            {!m.streaming && !!m.chips?.length && (
              <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label="Suggested questions">
                {m.chips.map((c) => (
                  <button key={c} type="button" disabled={busy} onClick={() => onChip(c)} className="min-h-9 rounded-full border border-border bg-raised px-3.5 text-label font-medium transition-[transform,background-color] duration-150 ease-out hover:bg-active active:scale-[0.97] disabled:opacity-50 motion-reduce:transition-none">
                    {c}
                  </button>
                ))}
              </div>
            )}
            {!m.streaming && <TimingLine m={m} />}
          </>
        )}
      </div>
    </motion.li>
  );
}

// the public demo answers on the deterministic path only: no local model, no cloud
const DEMO = import.meta.env.VITE_DEMO === "1";

/** First questions for an empty chat: each one maps to a read tool, in the app and in the demo. */
const STARTERS: [string, string][] = [
  ["Spending", "How much did I spend last month?"],
  ["A category", "What did I spend on Eating out last month?"],
  ["Subscriptions", "Which subscriptions do I pay for?"],
  ["Insights", "Anything unusual this month?"],
];

export function Assistant() {
  const chat = useChat(); // lives above the router: a turn in flight survives a tab change
  const [draft, setDraft] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const reduceMotion = useReducedMotion();
  const [cloud, setCloud] = useState(false);
  const spend = useQuery({
    queryKey: ["spend"],
    enabled: !DEMO,
    retry: false,
    queryFn: async () => {
      const res = await fetch("/api/spend");
      if (!res.ok) throw new Error(String(res.status));
      return (await res.json()) as components["schemas"]["BucketOut"][];
    },
  });
  const capped = spend.data?.some((b) => b.bucket === "gateway_chat" && b.state === "capped") ?? false;

  // load the local model while the user types, so the first turn skips the load;
  // a beacon, not fetch: nothing waits on it and nothing is shown
  useEffect(() => {
    if (DEMO) return; // the demo has no model to warm
    navigator.sendBeacon?.("/api/chat/warm");
    return () => void navigator.sendBeacon?.("/api/chat/cool"); // the chat closed: free the model's memory
  }, []);

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end", behavior: reduceMotion ? "auto" : "smooth" });
  }, [chat.messages, reduceMotion]);

  // a 404 resets the conversation, and a new thread starts local
  useEffect(() => {
    if (chat.thread === null) setCloud(false);
  }, [chat.thread]);

  function say(text: string) {
    // cloud rides along only once the toggle is on; switching off a thread that used it sends false
    const optIn = cloud && !capped ? true : chat.messages.some((m) => m.path === "cloud") ? false : undefined;
    return sendMessage(text, optIn);
  }
  function send(e: React.FormEvent) {
    e.preventDefault();
    if (say(draft.trim())) setDraft("");
  }

  return (
    <Card className="flex min-h-[60dvh] flex-col p-0 sm:p-0">
      {DEMO && (
        <p className="px-4 pt-4 text-label text-muted-foreground sm:px-6 sm:pt-5">Answered from tables (demo)</p>
      )}
      <ol aria-label="Conversation" aria-live="polite" aria-busy={chat.busy} className="flex flex-1 flex-col gap-3 p-4 sm:p-6">
        {chat.messages.length === 0 && (
          <li className="m-auto flex w-full max-w-xl flex-col items-center py-10 text-center sm:py-14">
            <span className="grid size-12 place-items-center rounded-2xl bg-primary text-primary-foreground" aria-hidden="true">
              <MessageCircle className="size-6" />
            </span>
            <p className="mt-4 text-heading">Ask about your money</p>
            <p className="mt-1 max-w-sm text-label text-muted-foreground">
              Every figure comes from your ledger. Changes only happen when you confirm them.
            </p>
            <div className="mt-8 grid w-full gap-2 text-left sm:grid-cols-2" role="group" aria-label="Try asking">
              {STARTERS.map(([kind, q]) => (
                <button
                  key={q}
                  type="button"
                  disabled={chat.busy}
                  onClick={() => say(q)}
                  className="group flex min-h-16 flex-col justify-between gap-1 rounded-2xl border border-border bg-raised px-4 py-3 text-left transition-[transform,background-color,border-color] duration-150 ease-out hover:border-ring hover:bg-active active:scale-[0.98] disabled:opacity-50 motion-reduce:transition-none"
                >
                  <span className="text-meta font-semibold uppercase tracking-wider text-muted-foreground">{kind}</span>
                  <span className="text-row font-medium">{q}</span>
                </button>
              ))}
            </div>
          </li>
        )}
        {chat.messages.map((m, i) => (
          <Bubble key={m.id} m={m} thread={chat.thread} cloudIndex={chat.messages.slice(0, i).filter((x) => x.path === "cloud").length} busy={chat.busy} decision={m.proposalId != null ? chat.decisions[m.proposalId] : undefined} onDecide={decide} onChip={say} />
        ))}
      </ol>
      {!DEMO && <CloudToggle on={cloud} capped={capped} disabled={chat.busy} onChange={setCloud} />}
      <div ref={end} className="scroll-mb-40 sm:scroll-mb-24" />
      <form onSubmit={send} className="sticky bottom-24 flex items-end gap-2 rounded-b-card border-t border-border bg-card p-3 sm:bottom-0">
        <label htmlFor="chat-input" className="sr-only">
          Message the Assistant
        </label>
        <textarea
          id="chat-input"
          rows={1}
          maxLength={2000}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) send(e);
          }}
          placeholder="Ask about your money"
          className="max-h-32 min-h-11 flex-1 resize-none rounded-3xl bg-raised px-4 py-2.5 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <button
          type="submit"
          aria-label="Send"
          disabled={chat.busy || !draft.trim()}
          className="inline-flex size-11 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground transition-transform active:scale-[0.97] disabled:opacity-40"
        >
          <ArrowUp className="size-5" aria-hidden="true" />
        </button>
      </form>
    </Card>
  );
}
