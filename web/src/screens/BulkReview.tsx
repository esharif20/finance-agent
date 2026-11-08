import { Undo2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { type ReviewItem, type ReviewQueueOut, answerReviewBatch, undoReview } from "@/api/client";
import { Money } from "@/components/Money";
import { Card } from "@/components/ui/card";
import {
  type BulkGroup,
  GROUP_CAP,
  groupBySuggestion,
  isBig,
  pages,
  payments,
  selected,
  shown,
  ticked,
} from "@/lib/bulkReview";
import { btn, useReviewWrite } from "./ReviewQueue";

const kbd = "hidden rounded-md bg-card px-1.5 text-xs sm:inline font-medium text-muted-foreground";
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** Bulk Review: Category items grouped by the model's top suggestion; one tap accepts a group's ticked rows. */
export function BulkReview({ queue, others }: { queue: ReviewQueueOut; others: () => void }) {
  const groups = groupBySuggestion(queue.items);
  // rows the user flipped from their default: ticked ones the user unticked, and the other way round
  const [flipped, setFlipped] = useState<ReadonlySet<string>>(new Set());
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set()); // big groups whose rows are showing
  const [pageAt, setPageAt] = useState<ReadonlyMap<string, number>>(new Map());
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);
  const [batch, setBatch] = useState(0); // answers the last accept wrote; Undo takes them all back
  const refocus = useRef<number | null>(null); // the accepted group's place, so focus does not fall to <body>

  const act = useReviewWrite<{ call: () => Promise<ReviewQueueOut>; text: string; batch?: boolean }>(
    (q, { text, batch: wrote }) => {
      setNote({ text: q.message ?? text });
      setBatch(wrote ? q.applied : 0);
    },
    (e) => setNote({ text: `${e.message} Showing the latest queue.`, bad: true }),
  );

  const pageOf = (g: BulkGroup) => Math.min(pageAt.get(g.key) ?? 0, pages(g) - 1);
  // a big group is accepted only once its rows are on screen, and then only the page shown
  const canAccept = (g: BulkGroup) => !isBig(g) || open.has(g.key);

  const accept = (g: BulkGroup) => {
    const items = selected(g, flipped, pageOf(g));
    if (!items.length || !canAccept(g)) return;
    const at = document.activeElement?.closest("[data-group]");
    refocus.current = at ? groups.indexOf(g) : null;
    act.run({
      call: () => answerReviewBatch(items, g.category),
      text: `Accepted ${plural(items.length, "Merchant", "Merchants")} as ${g.category[1]}.`,
      batch: true,
    });
  };
  const undo = () => act.run({ call: () => undoReview(batch || 1), text: "Undid the last change." });
  const flip = (id: string) =>
    setFlipped((u) => {
      const next = new Set(u);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  const toggleOpen = (key: string) =>
    setOpen((o) => {
      const next = new Set(o);
      if (!next.delete(key)) next.add(key);
      return next;
    });

  // keep keyboard focus on the Accept button in the same place once the accepted rows have gone
  useEffect(() => {
    const at = refocus.current;
    if (at === null) return;
    refocus.current = null;
    const buttons = document.querySelectorAll<HTMLElement>('[data-group] button[aria-keyshortcuts="A"]');
    buttons[Math.min(at, buttons.length - 1)]?.focus();
  }, [queue.items]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || e.repeat) return; // a held key must not accept the next group too
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

  const total = groups.reduce((n, g) => n + g.items.filter((i) => ticked(g, flipped, i.id)).length, 0);
  const rest = queue.items.length - groups.reduce((n, g) => n + g.items.length, 0);

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
          <button type="button" onClick={undo} aria-disabled={act.isPending} aria-keyshortcuts="U" className={`${btn} glass`}>
            <Undo2 className="size-4" aria-hidden="true" />
            Undo {plural(batch, "answer", "answers")}
            <kbd className={kbd}>U</kbd>
          </button>
        )}
      </div>

      {groups.map((g) => {
        const picked = selected(g, flipped, pageOf(g));
        const big = isBig(g);
        const expanded = open.has(g.key);
        const page = pageOf(g);
        const rows = big ? (expanded ? shown(g, page) : []) : g.items;
        const blocked = act.isPending || picked.length === 0 || !canAccept(g);
        const count = `${plural(picked.length, "merchant", "merchants")} (${plural(payments(picked), "payment", "payments")})`;
        return (
          <Card key={g.key} data-group={g.key} aria-labelledby={`g-${g.key}`} className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 id={`g-${g.key}`} className="text-title">
                  {g.category[1]}
                </h2>
                <p className="text-sm text-muted-foreground">
                  {g.category[0]} · {plural(g.items.length, "Merchant", "Merchants")} ·{" "}
                  {plural(payments(g.items), "payment", "payments")}
                </p>
              </div>
              <button
                type="button"
                onClick={() => accept(g)}
                aria-disabled={blocked}
                title={big && !expanded ? `Show the ${g.items.length} Merchants first` : undefined}
                aria-keyshortcuts="A"
                className={`${btn} bg-foreground text-background ${blocked ? "opacity-50" : ""}`}
              >
                {big ? `Accept ${picked.length} shown (${plural(payments(picked), "payment", "payments")})` : `Accept ${count}`} as{" "}
                {g.category[1]}
                <kbd className={`${kbd} bg-transparent text-background/70`}>A</kbd>
              </button>
            </div>
            {big && (
              <div className="flex flex-wrap items-center gap-2">
                <button type="button" aria-expanded={expanded} onClick={() => toggleOpen(g.key)} className={btn}>
                  {expanded ? "Hide Merchants" : `Show the ${g.items.length} Merchants`}
                </button>
                {expanded && (
                  <>
                    <span className="text-sm text-muted-foreground">
                      {page * GROUP_CAP + 1} to {Math.min((page + 1) * GROUP_CAP, g.items.length)} of {g.items.length}
                    </span>
                    <button
                      type="button"
                      disabled={page === 0}
                      onClick={() => setPageAt(new Map(pageAt).set(g.key, page - 1))}
                      className={btn}
                    >
                      Previous {GROUP_CAP}
                    </button>
                    <button
                      type="button"
                      disabled={page >= pages(g) - 1}
                      onClick={() => setPageAt(new Map(pageAt).set(g.key, page + 1))}
                      className={btn}
                    >
                      Next {GROUP_CAP}
                    </button>
                  </>
                )}
              </div>
            )}
            {rows.length > 0 && (
              <ul className="flex flex-col">
                {rows.map((i) => (
                  <Row key={i.id} item={i} on={ticked(g, flipped, i.id)} toggle={() => flip(i.id)} />
                ))}
              </ul>
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
          <span className="break-words font-medium">{item.display_name || item.title || "Unnamed payment"}</span>
          <span className="text-xs text-muted-foreground">{plural(item.weight, "transaction", "transactions")}</span>
        </span>
        <Money value={{ minor: item.spend_minor, currency: item.currency }} className="shrink-0 text-right text-sm" />
      </label>
    </li>
  );
}
