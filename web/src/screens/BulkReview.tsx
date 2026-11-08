import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Undo2 } from "lucide-react";
import { useEffect, useState } from "react";
import { type ReviewItem, type ReviewQueueOut, answerReviewBatch, undoReview } from "@/api/client";
import { Money } from "@/components/Money";
import { Card } from "@/components/ui/card";
import { type BulkGroup, groupBySuggestion, selected, spendOf } from "@/lib/bulkReview";
import { REVIEW_KEY } from "./ReviewQueue";

const btn =
  "inline-flex min-h-11 items-center justify-center gap-2 rounded-full px-4 text-sm font-semibold transition-[background-color,transform] duration-150 ease-out active:scale-[0.97] disabled:opacity-50 [@media(hover:hover)]:hover:bg-active";
const kbd = "hidden rounded-md bg-card px-1.5 text-xs sm:inline font-medium text-muted-foreground";
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const SHOWN = 12; // rows a group shows before "Show all"

/** Bulk Review: Category items grouped by the model's top suggestion; one tap accepts a group's ticked rows. */
export function BulkReview({ queue, others }: { queue: ReviewQueueOut; others: () => void }) {
  const client = useQueryClient();
  const groups = groupBySuggestion(queue.items);
  const [unticked, setUnticked] = useState<ReadonlySet<string>>(new Set());
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set()); // groups showing every row
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);
  const [batch, setBatch] = useState(0); // answers the last accept wrote; Undo takes them all back

  const act = useMutation({
    mutationFn: ({ call }: { call: () => Promise<ReviewQueueOut>; text: string; batch?: boolean }) => call(),
    onSuccess: (q, { text, batch: wrote }) => {
      client.setQueryData(REVIEW_KEY, q);
      setNote({ text: q.message ?? text });
      setBatch(wrote ? q.applied : 0);
    },
    onError: (e) => setNote({ text: `${e.message} Showing the latest queue.`, bad: true }),
    onSettled: () => client.invalidateQueries(),
  });

  const accept = (g: BulkGroup) => {
    const items = selected(g, unticked);
    if (!items.length || act.isPending) return;
    act.mutate({
      call: () => answerReviewBatch(items, g.category),
      text: `Accepted ${plural(items.length, "Merchant", "Merchants")} as ${g.category[1]}.`,
      batch: true,
    });
  };
  const undo = () => {
    if (!act.isPending) act.mutate({ call: () => undoReview(batch || 1), text: "Undid the last change." });
  };
  const toggle = (id: string) =>
    setUnticked((u) => {
      const next = new Set(u);
      if (!next.delete(id)) next.add(id);
      return next;
    });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const k = e.key.toLowerCase();
      const t = e.target;
      if (t instanceof HTMLElement && (t.isContentEditable || /^(TEXTAREA|SELECT)$/.test(t.tagName))) return;
      if (k === "u" && batch) undo();
      else if (k === "a") {
        // the group holding focus, else the biggest one
        const key = (document.activeElement as HTMLElement | null)?.closest<HTMLElement>("[data-group]")?.dataset.group;
        const g = groups.find((x) => x.key === key) ?? groups[0];
        if (g) accept(g);
      } else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const total = groups.reduce((n, g) => n + selected(g, unticked).length, 0);
  const rest = queue.items.length - groups.reduce((n, g) => n + g.items.length, 0);
  const currency = groups[0]?.items[0]?.currency ?? "GBP";

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-1">
        <p className="text-sm font-semibold">{plural(queue.items.length, "item", "items")} to review</p>
        <p className="text-sm text-muted-foreground" aria-live="polite">
          {plural(total, "Merchant", "Merchants")} ticked in {plural(groups.length, "group", "groups")}
        </p>
      </div>

      <div className="flex min-h-11 flex-wrap items-center justify-between gap-3">
        <p
          role="status"
          aria-live="polite"
          className={`text-sm ${note?.bad ? "rounded-[10px] bg-warning px-3 py-1.5 text-warning-foreground" : "text-muted-foreground"}`}
        >
          {note?.text}
        </p>
        {batch > 0 && (
          <button type="button" onClick={undo} disabled={act.isPending} aria-keyshortcuts="U" className={`${btn} glass`}>
            <Undo2 className="size-4" aria-hidden="true" />
            Undo {plural(batch, "answer", "answers")}
            <kbd className={kbd}>U</kbd>
          </button>
        )}
      </div>

      {groups.map((g) => {
        const picked = selected(g, unticked);
        const all = open.has(g.key);
        const rows = all ? g.items : g.items.slice(0, SHOWN);
        return (
          <Card key={g.key} data-group={g.key} aria-labelledby={`g-${g.key}`} className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 id={`g-${g.key}`} className="text-title">
                  {g.category[1]}
                </h2>
                <p className="text-sm text-muted-foreground">
                  {g.category[0]} · {plural(g.items.length, "Merchant", "Merchants")} ·{" "}
                  <Money value={{ minor: spendOf(g.items), currency }} />
                </p>
              </div>
              <button
                type="button"
                onClick={() => accept(g)}
                disabled={act.isPending || picked.length === 0}
                aria-keyshortcuts="A"
                className={`${btn} bg-foreground text-background`}
              >
                Accept {picked.length} as {g.category[1]}
                <kbd className={`${kbd} bg-transparent text-background/70`}>A</kbd>
              </button>
            </div>
            <ul className="flex flex-col">
              {rows.map((i) => (
                <Row key={i.id} item={i} on={!unticked.has(i.id)} toggle={() => toggle(i.id)} />
              ))}
            </ul>
            {g.items.length > SHOWN && (
              <button
                type="button"
                aria-expanded={all}
                onClick={() =>
                  setOpen((o) => {
                    const next = new Set(o);
                    if (!next.delete(g.key)) next.add(g.key);
                    return next;
                  })
                }
                className={`${btn} self-start`}
              >
                {all ? "Show fewer" : `Show all ${g.items.length}`}
              </button>
            )}
          </Card>
        );
      })}

      {groups.length === 0 && <p className="text-muted-foreground">Nothing left to accept in bulk.</p>}

      {rest > 0 && (
        <button type="button" onClick={others} className={`${btn} glass self-start`}>
          Review the other {rest} one at a time
        </button>
      )}

      <p
        role="group"
        aria-label="Keyboard shortcuts"
        className="hidden flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground sm:flex"
      >
        <span>
          <kbd className={kbd}>Tab</kbd> move
        </span>
        <span>
          <kbd className={kbd}>Space</kbd> tick or untick
        </span>
        <span>
          <kbd className={kbd}>A</kbd> accept the group
        </span>
        <span>
          <kbd className={kbd}>U</kbd> undo
        </span>
      </p>
    </div>
  );
}

function Row({ item, on, toggle }: { item: ReviewItem; on: boolean; toggle: () => void }) {
  return (
    <li>
      <label className="flex min-h-11 cursor-pointer items-center gap-3 rounded-[10px] px-1 [@media(hover:hover)]:hover:bg-active">
        <input type="checkbox" checked={on} onChange={toggle} className="size-5 shrink-0 accent-[var(--foreground)]" />
        <span className="flex min-w-0 flex-1 flex-col py-1.5">
          <span className="break-words font-medium">{item.title || "Unnamed payment"}</span>
          <span className="text-xs text-muted-foreground">{plural(item.weight, "transaction", "transactions")}</span>
        </span>
        <Money value={{ minor: item.spend_minor, currency: item.currency }} className="shrink-0 text-right text-sm" />
      </label>
    </li>
  );
}
