import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronLeft, ChevronRight, CircleCheck, SkipForward, Undo2 } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import {
  type ReviewItem,
  type ReviewQueueOut,
  answerReview,
  fetchAutoBatch,
  fetchCategories,
  fetchReviewQueue,
  skipReview,
  undoAutoBatch,
  undoReview,
} from "@/api/client";
import { Money } from "@/components/Money";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { BULK_MIN, bulkable } from "@/lib/bulkReview";
import { BulkReview } from "./BulkReview";
import { AlwaysAskExtras, SplitRecheck, SplitRefund } from "./ReviewSplitItems";

export const REVIEW_KEY = ["review-queue"] as const;
const AUTO_KEY = ["review-queue", "auto-batch"] as const;

type Cat = [string, string];
const TYPE_LABEL: Record<string, string> = {
  category: "Category",
  subscription: "Is this a subscription?",
  possible_duplicate: "Possible duplicate",
  always_ask: "What was this?",
  split_recheck: "Split needs a re-check",
  split_refund: "Which part?",
};
const CHOICE_LABEL: Record<string, string> = { yes: "Yes", no: "No", keep_both: "Keep both", revert: "Undo the split" };

// the latest picks lead the suggestions; shortcut: replaced by the categoriser's ranking (slice 2)
const recent: Cat[] = [];

const typing = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));

// aria-disabled (not disabled) while a write is pending: the button keeps focus and does not flash
export const btn =
  "inline-flex min-h-11 items-center justify-center gap-2 rounded-full px-4 text-sm font-semibold transition-[background-color,transform] duration-150 ease-out active:scale-[0.97] disabled:opacity-50 aria-disabled:cursor-progress aria-disabled:active:scale-100 [@media(hover:hover)]:hover:bg-active";
const DOUBLE_PRESS_MS = 200;
const kbd = "hidden rounded-md bg-card px-1.5 text-xs sm:inline font-medium text-muted-foreground";

/**
 * Review writes run one after another, in click order, and a click is never dropped: one made while a
 * write is in flight waits its turn. A write naming an `item` takes it off the shown queue at once, so
 * the next item is there before the server replies; the same item is never written twice (a double
 * click or key repeat in one tick, a second accept of the same rows). The last reply is the new queue, so it is not fetched again and only
 * the other screens refresh, in the background: the next click never waits on them. A failure refetches
 * everything, the queue too, which puts back what the server did not take.
 */
export function useReviewWrite<V extends { call: () => Promise<ReviewQueueOut>; items?: string[] }>(
  onOk: (q: ReviewQueueOut, v: V) => void,
  onFail: (e: Error) => void,
) {
  const client = useQueryClient();
  const chain = useRef<Promise<void>>(Promise.resolve());
  const inFlight = useRef(0);
  const taken = useRef(new Set<string>()); // items written or waiting, until the server's queue is back
  const [isPending, setPending] = useState(false);
  const run = (v: V) => {
    const ids = v.items ?? [];
    if (ids.some((id) => taken.current.has(id))) return;
    if (ids.length) {
      for (const id of ids) taken.current.add(id);
      client.setQueryData<ReviewQueueOut>(REVIEW_KEY, (q) => {
        if (!q) return q;
        const left = q.items.filter((i) => !ids.includes(i.id));
        return { ...q, items: left, remaining: Math.max(q.remaining - (q.items.length - left.length), 0) };
      });
    }
    inFlight.current += 1;
    setPending(true);
    chain.current = chain.current
      .then(v.call)
      .then(
        (q) => {
          inFlight.current -= 1;
          // a queued write's item stays hidden until its own reply: only the last reply is the queue
          if (inFlight.current === 0) {
            client.setQueryData(REVIEW_KEY, q);
            taken.current.clear();
          }
          onOk(q, v);
          void client.invalidateQueries({ predicate: (x) => x.queryKey.length > 1 || x.queryKey[0] !== REVIEW_KEY[0] });
        },
        (e: unknown) => {
          inFlight.current -= 1;
          for (const id of ids) taken.current.delete(id);
          onFail(e instanceof Error ? e : new Error(String(e)));
          void client.invalidateQueries();
        },
      )
      .finally(() => setPending(inFlight.current > 0));
  };
  return { run, isPending };
}

/** The queue's count, shared by the nav badge and the Overview line. */
export function useReviewCount(): number {
  const { data } = useQuery({ queryKey: REVIEW_KEY, queryFn: fetchReviewQueue, refetchOnWindowFocus: true });
  return data?.remaining ?? 0;
}

// the local model's nearest-neighbour suggestions lead (#114); they are only buttons
function suggestions(groups: { name: string; subs: string[] }[], model: Cat[] = []): Cat[] {
  const all: Cat[] = groups
    .filter((g) => g.name !== "Uncategorised")
    .flatMap((g) => g.subs.map((s): Cat => [g.name, s]));
  const seen = new Set<string>();
  return [...model, ...recent, ...all]
    .filter(([g, s]) => !seen.has(`${g}/${s}`) && seen.add(`${g}/${s}`))
    .slice(0, 3);
}

/** After a sync: what the model filed by itself, with one way back. The count is the API's. */
function AutoApplied() {
  const reduce = useReducedMotion();
  const batch = useQuery({ queryKey: AUTO_KEY, queryFn: fetchAutoBatch });
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);
  const undo = useReviewWrite<{ call: () => Promise<ReviewQueueOut> }>(
    (q) => {
      const back = `${q.applied} ${q.applied === 1 ? "merchant" : "merchants"}`;
      setNote({ text: q.applied ? `Put ${back} back in the queue.` : (q.message ?? "Nothing to undo.") });
    },
    (e) => setNote({ text: `${e.message} Nothing was changed.`, bad: true }),
  );
  const n = batch.data?.count ?? 0;
  const fade = reduce ? { duration: 0 } : { duration: 0.2, ease: [0.23, 1, 0.32, 1] as const };
  return (
    <AnimatePresence initial={false}>
      {(n > 0 || note) && (
        <motion.div
          key="auto-applied"
          initial={{ opacity: 0, y: reduce ? 0 : -6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, transition: { duration: reduce ? 0 : 0.14 } }}
          transition={fade}
          className="mx-auto flex w-full max-w-xl flex-wrap items-center justify-between gap-x-3 gap-y-1 px-1"
        >
          <p
            role="status"
            aria-live="polite"
            className={`text-sm ${note?.bad ? "rounded-[10px] bg-warning px-3 py-1.5 text-warning-foreground" : "text-muted-foreground"}`}
          >
            {note?.text ?? `Auto-categorised ${n} ${n === 1 ? "merchant" : "merchants"}`}
          </p>
          {n > 0 && (
            <button type="button" aria-label="Undo all auto-categorising" aria-disabled={undo.isPending} onClick={() => undo.run({ call: undoAutoBatch })} className={`${btn} glass`}>
              <Undo2 className="size-4" aria-hidden="true" />
              Undo all
            </button>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/** Bulk by default when a queue is mostly suggested Category rows; one at a time otherwise, or by choice. */
export function ReviewQueue() {
  const queue = useQuery({ queryKey: REVIEW_KEY, queryFn: fetchReviewQueue });
  const auto = useQuery({ queryKey: AUTO_KEY, queryFn: fetchAutoBatch });
  const [mode, setMode] = useState<"bulk" | "single" | null>(null);
  if (!queue.data) return <OneAtATime />; // loading and error states live there
  // the "Auto-categorised" line sits above the list: wait for it, or it shoves the buttons down mid-click
  if (auto.isPending) return <Skeleton className="h-80 w-full rounded-card" />;
  const eligible = queue.data.items.filter(bulkable).length >= BULK_MIN;
  // Bulk sticks once chosen: accepting the last group must not swap the screen (and drop its Undo) under the user
  if (eligible && mode === null) setMode("bulk");
  const view = mode ?? "single";
  return (
    <div className="flex flex-col gap-4">
      <AutoApplied />
      {(eligible || mode === "single") && (
        <div role="group" aria-label="View" className="mx-auto flex gap-1 self-center rounded-full bg-raised p-1">
          {(
            [
              ["bulk", "Bulk"],
              ["single", "One at a time"],
            ] as const
          ).map(([m, text]) => (
            <button
              key={m}
              type="button"
              aria-pressed={view === m}
              onClick={() => setMode(m)}
              className={`${btn} ${view === m ? "bg-card" : ""}`}
            >
              {text}
            </button>
          ))}
        </div>
      )}
      {view === "bulk" ? <BulkReview queue={queue.data} others={() => setMode("single")} /> : <OneAtATime />}
    </div>
  );
}

function OneAtATime() {
  const queue = useQuery({ queryKey: REVIEW_KEY, queryFn: fetchReviewQueue });
  const categories = useQuery({ queryKey: ["categories"], queryFn: fetchCategories });
  const [at, setAt] = useState(0);
  const [all, setAll] = useState(true); // "Apply to N similar" (the Merchant) or just this one
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);

  const act = useReviewWrite<{ call: () => Promise<ReviewQueueOut>; text: string; items?: string[] }>(
    (q, { text }) => setNote({ text: q.message ?? text }), // an undo that found nothing to undo says why
    (e) => setNote({ text: `${e.message} Showing the latest queue.`, bad: true }),
  );

  const items = queue.data?.items ?? [];
  const idx = Math.min(at, Math.max(items.length - 1, 0));
  const item: ReviewItem | undefined = items[idx];
  const groups = categories.data ?? [];
  const asksCategory = item?.type === "category" || item?.type === "always_ask";
  const picks = asksCategory ? suggestions(groups, (item?.suggestions ?? []) as Cat[]) : [];
  const many = (item?.weight ?? 0) > 1 && item?.transaction_ids.length;
  const justOne = !all && !!many;
  // The next item shows the moment one is answered, so the second press of a double click would land
  // on it: an answer to another item that soon after the last is that second press, and is ignored.
  const last = useRef({ id: "", at: -Infinity });
  const fresh = (id: string) => {
    const now = performance.now();
    if (id !== last.current.id && now - last.current.at < DOUBLE_PRESS_MS) return false;
    last.current = { id, at: now };
    return true;
  };

  const pick = (cat: Cat) => {
    if (!item || !fresh(item.id)) return;
    recent.unshift(cat);
    recent.splice(8);
    const txn = justOne ? item.transaction_ids[0] : undefined;
    act.run({
      call: () => answerReview(item, { category: cat, ...(txn === undefined ? {} : { transaction_id: txn }) }),
      text: `${item.display_name || "This payment"}: ${cat[1]}${justOne ? ", just this one" : many ? `, ${item.weight} transactions` : ""}.`,
      items: [item.id],
    });
    setAll(true); // the next item starts on its whole Merchant again
  };
  const choose = (choice: string) => {
    if (!item || !fresh(item.id)) return;
    act.run({
      call: () => answerReview(item, { choice }),
      text: `${item.display_name || TYPE_LABEL[item.type] || "Item"}: ${CHOICE_LABEL[choice] ?? choice}.`,
      items: [item.id],
    });
  };
  const skip = () => {
    if (!item || !fresh(item.id)) return;
    act.run({ call: () => skipReview(item), text: `Skipped ${item.display_name || "this item"}.`, items: [item.id] });
  };
  const undo = () => {
    act.run({ call: () => undoReview(), text: "Undid the last change." });
  };
  const move = (by: number) => setAt(Math.max(0, Math.min(items.length - 1, idx + by)));

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return;
      const k = e.key.toLowerCase();
      if (e.repeat && !k.startsWith("arrow")) return; // holding a key must not answer the next item too
      const n = Number(k);
      if (n >= 1 && n <= 3) {
        const cat = picks[n - 1];
        const choice = item?.choices[n - 1];
        if (cat) pick(cat);
        else if (choice) choose(choice);
        else return;
      } else if (k === "s") skip();
      else if (k === "u") undo();
      else if (k === "arrowright") move(1);
      else if (k === "arrowleft") move(-1);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (queue.isPending) return <Skeleton className="h-80 w-full rounded-card" />;
  if (queue.isError) return <p role="alert">Couldn't load the review queue. Try again in a moment.</p>;

  const status = (
    <p
      role="status"
      aria-live="polite"
      className={`min-h-6 text-sm ${note?.bad ? "rounded-[10px] bg-warning px-3 py-1.5 text-warning-foreground" : "text-muted-foreground"}`}
    >
      {note?.text}
    </p>
  );

  if (!item) {
    return (
      <div className="mx-auto flex w-full max-w-xl flex-col gap-4">
        <Card className="flex flex-col items-center gap-3 py-14 text-center">
          <CircleCheck className="size-10 text-positive" aria-hidden="true" />
          <h2 className="text-heading">Nothing to review</h2>
          <p className="max-w-sm text-muted-foreground">
            New decisions appear here after a sync or an import. Until then, everything is filed.
          </p>
        </Card>
        <div className="flex items-center justify-between gap-3">
          {status}
          {note && (
            <button type="button" onClick={undo} aria-keyshortcuts="U" className={`${btn} glass`}>
              <Undo2 className="size-4" aria-hidden="true" />
              Undo
              <kbd className={kbd}>U</kbd>
            </button>
          )}
        </div>
      </div>
    );
  }

  const label = TYPE_LABEL[item.type] ?? item.type.replaceAll("_", " ");
  const title = item.display_name || label;
  return (
    <div className="mx-auto flex w-full max-w-xl flex-col gap-4">
      <div className="flex items-baseline justify-between gap-3 px-1">
        <p className="text-sm font-semibold">{items.length} to review</p>
        <p className="text-sm text-muted-foreground">
          {idx + 1} of {items.length}
        </p>
      </div>

      {status}

      <Card data-still aria-labelledby="review-title" className="flex flex-col gap-5">
        <div className="flex flex-col gap-1">
          <p className="text-sm text-muted-foreground">
            {label}
            {item.skipped && " (skipped earlier)"}
          </p>
          <h2 id="review-title" className="text-title break-words">
            {title}
          </h2>
          {item.weight > 0 && (
            <p className="text-muted-foreground">
              {item.weight} {item.weight === 1 ? "transaction" : "transactions"}
            </p>
          )}
        </div>

        {item.type === "life_change" && item.before_minor != null && item.after_minor != null && (
          <dl className="grid grid-cols-2 gap-3 rounded-[14px] bg-raised p-4">
            {[
              ["Before", item.before_minor],
              ["After", item.after_minor],
            ].map(([k, v]) => (
              <div key={k as string}>
                <dt className="text-sm text-muted-foreground">{k} (a month)</dt>
                <dd>
                  <Money value={{ minor: v as number, currency: item.currency }} className="text-title" />
                </dd>
              </div>
            ))}
          </dl>
        )}

        {asksCategory && (
          <>
            {!!many && (
              <div role="group" aria-label="Scope" className="flex gap-1 self-start rounded-full bg-raised p-1">
                {[
                  [true, `Apply to ${item.weight} similar`],
                  [false, "Just this one"],
                ].map(([on, text]) => (
                  <button
                    key={String(on)}
                    type="button"
                    aria-pressed={all === on}
                    onClick={() => setAll(on as boolean)}
                    className={`${btn} ${all === on ? "bg-card" : ""}`}
                  >
                    {text as string}
                  </button>
                ))}
              </div>
            )}
            <div className="grid gap-2 sm:grid-cols-3">
              {picks.map((cat, i) => (
                <button
                  key={cat.join("/")}
                  type="button"
                  title={cat[0]}
                  aria-keyshortcuts={String(i + 1)}
                 
                  onClick={() => pick(cat)}
                  className={`${btn} min-h-14 justify-between bg-raised`}
                >
                  {cat[1]}
                  <kbd className={kbd}>{i + 1}</kbd>
                </button>
              ))}
            </div>
            <label className="relative">
              <span className="sr-only">Another category</span>
              <select
                value=""
               
                onChange={(e) => {
                  const [g, s] = e.target.value.split(".").map(Number);
                  const sub = groups[g ?? -1]?.subs[s ?? -1];
                  if (sub !== undefined) pick([groups[g as number]!.name, sub]);
                }}
                className="min-h-11 w-full appearance-none rounded-full border border-[var(--control)] bg-card px-4 pr-10 text-sm font-medium"
              >
                <option value="">Another category…</option>
                {groups.map((g, gi) => (
                  <optgroup key={g.name} label={g.name}>
                    {g.subs.map((s, si) => (
                      <option key={s} value={`${gi}.${si}`}>
                        {s}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
              <ChevronDown
                className="pointer-events-none absolute top-1/2 right-4 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden="true"
              />
            </label>
            {item.type === "always_ask" && <AlwaysAskExtras item={item} groups={groups} />}
          </>
        )}

        {item.type === "split_recheck" && <SplitRecheck item={item} groups={groups} busy={act.isPending} choose={choose} />}
        {item.type === "split_refund" && <SplitRefund item={item} groups={groups} busy={act.isPending} pick={pick} />}

        {!asksCategory && item.type !== "split_recheck" && item.type !== "split_refund" && (
          <div className="grid gap-2 sm:grid-cols-2">
            {item.choices.map((c, i) => (
              <button
                key={c}
                type="button"
                aria-keyshortcuts={String(i + 1)}
               
                onClick={() => choose(c)}
                className={`${btn} min-h-14 justify-between bg-raised`}
              >
                {CHOICE_LABEL[c] ?? c}
                <kbd className={kbd}>{i + 1}</kbd>
              </button>
            ))}
          </div>
        )}
      </Card>

      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={skip} aria-keyshortcuts="S" className={`${btn} glass`}>
          <SkipForward className="size-4" aria-hidden="true" />
          Skip
          <kbd className={kbd}>S</kbd>
        </button>
        <button type="button" onClick={undo} aria-keyshortcuts="U" className={`${btn} glass`}>
          <Undo2 className="size-4" aria-hidden="true" />
          Undo
          <kbd className={kbd}>U</kbd>
        </button>
        <div className="ml-auto flex gap-2">
          <button
            type="button"
            aria-label="Previous item"
            aria-keyshortcuts="ArrowLeft"
            disabled={idx === 0}
            onClick={() => move(-1)}
            className={`${btn} glass min-w-11 px-3`}
          >
            <ChevronLeft className="size-4" aria-hidden="true" />
          </button>
          <button
            type="button"
            aria-label="Next item"
            aria-keyshortcuts="ArrowRight"
            disabled={idx >= items.length - 1}
            onClick={() => move(1)}
            className={`${btn} glass min-w-11 px-3`}
          >
            <ChevronRight className="size-4" aria-hidden="true" />
          </button>
        </div>
      </div>

      <p
        role="group"
        aria-label="Keyboard shortcuts"
        className="hidden flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground sm:flex"
      >
        <span>
          <kbd className={kbd}>1</kbd> <kbd className={kbd}>2</kbd> <kbd className={kbd}>3</kbd> choose
        </span>
        <span>
          <kbd className={kbd}>S</kbd> skip
        </span>
        <span>
          <kbd className={kbd}>U</kbd> undo
        </span>
        <span>
          <kbd className={kbd}>←</kbd> <kbd className={kbd}>→</kbd> move
        </span>
      </p>
    </div>
  );
}
