import { useQuery } from "@tanstack/react-query";
import { motion, useReducedMotion } from "motion/react";
import { ArrowUp, Check, X } from "lucide-react";
import { Fragment, useEffect, useReducer, useRef, useState } from "react";
import type { components } from "@/api/schema";
import { Money } from "@/components/Money";
import { Card } from "@/components/ui/card";
import { type Block, type Decision, type Message, friendlyLabel, initial, parseBlocks, parseSse, pathLabel, reduce } from "@/lib/chat";
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

function Bubble({ m, decision, busy, onDecide }: { m: Message; decision?: Decision; busy: boolean; onDecide: (id: number, d: Decision) => void }) {
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
            {m.text ? <Reply text={m.text} /> : m.error ? null : <span className="text-label text-muted-foreground">Thinking…</span>}
            {m.error && <p className="text-label text-warning-foreground" role="alert">{m.error}</p>}
            {m.proposalId != null && !m.streaming && (
              <ProposalCard id={m.proposalId} decision={decision} disabled={busy} onDecide={(d) => onDecide(m.proposalId as number, d)} />
            )}
            {m.path && !m.streaming && (
              <p className="mt-2 text-meta text-muted-foreground" data-testid="path">
                Answered by: {pathLabel(m.path, m.text)}
              </p>
            )}
          </>
        )}
      </div>
    </motion.li>
  );
}

const refusal = (status: number) =>
  status === 404 ? "That conversation has ended. Ask again to start a new one."
  : status === 409 ? "Nothing is waiting for confirmation."
  : status === 422 ? "That message can't be sent. Keep it under 2,000 characters."
  : "The Assistant isn't available right now. Check that the local model is running.";

export function Assistant() {
  const [chat, dispatch] = useReducer(reduce, initial);
  const [draft, setDraft] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const reduceMotion = useReducedMotion();

  // load the local model while the user types, so the first turn skips the load;
  // a beacon, not fetch: nothing waits on it and nothing is shown
  useEffect(() => {
    navigator.sendBeacon?.("/api/chat/warm");
  }, []);

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end", behavior: reduceMotion ? "auto" : "smooth" });
  }, [chat.messages, reduceMotion]);

  async function turn(url: string, body: object) {
    try {
      const res = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      if (!res.ok || !res.body) {
        if (res.status === 404) dispatch({ type: "reset" });
        return dispatch({ type: "fail", error: refusal(res.status) });
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let rest = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        const parsed = parseSse(rest, dec.decode(value, { stream: true }));
        rest = parsed.rest;
        for (const event of parsed.events) dispatch({ type: "event", event });
      }
      dispatch({ type: "event", event: { name: "done", data: {} } });
    } catch {
      dispatch({ type: "fail", error: refusal(500) });
    }
  }

  function send(e: React.FormEvent) {
    e.preventDefault();
    const text = draft.trim();
    if (!text || chat.busy) return;
    setDraft("");
    dispatch({ type: "send", text });
    void turn("/api/chat", chat.thread ? { message: text, thread_id: chat.thread } : { message: text });
  }

  function decide(proposalId: number, decision: Decision) {
    if (!chat.thread || chat.busy) return;
    dispatch({ type: "decide", proposalId, decision });
    void turn(`/api/chat/${chat.thread}/resume`, { decision });
  }

  return (
    <Card className="flex min-h-[60dvh] flex-col p-0 sm:p-0">
      <ol aria-label="Conversation" aria-live="polite" aria-busy={chat.busy} className="flex flex-1 flex-col gap-3 p-4 sm:p-6">
        {chat.messages.length === 0 && (
          <li className="m-auto max-w-sm py-16 text-center">
            <p className="text-heading">Ask about your money</p>
            <p className="mt-1 text-label text-muted-foreground">
              Every figure comes from your ledger. Changes only happen when you confirm them.
            </p>
          </li>
        )}
        {chat.messages.map((m) => (
          <Bubble key={m.id} m={m} busy={chat.busy} decision={m.proposalId != null ? chat.decisions[m.proposalId] : undefined} onDecide={decide} />
        ))}
      </ol>
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
