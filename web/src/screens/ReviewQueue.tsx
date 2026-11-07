import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronLeft, ChevronRight, CircleCheck, SkipForward, Undo2 } from "lucide-react";
import { useEffect, useState } from "react";
import {
  type ReviewItem,
  type ReviewQueueOut,
  answerReview,
  fetchCategories,
  fetchReviewQueue,
  skipReview,
  undoReview,
} from "@/api/client";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export const REVIEW_KEY = ["review-queue"] as const;

type Cat = [string, string];
const TYPE_LABEL: Record<string, string> = {
  category: "Category",
  subscription: "Is this a subscription?",
  possible_duplicate: "Possible duplicate",
};
const CHOICE_LABEL: Record<string, string> = { yes: "Yes", no: "No", keep_both: "Keep both" };

// the latest picks lead the suggestions; shortcut: replaced by the categoriser's ranking (slice 2)
const recent: Cat[] = [];

const typing = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));

const btn =
  "inline-flex min-h-11 items-center justify-center gap-2 rounded-full px-4 text-sm font-semibold transition-[background-color,transform] duration-150 ease-out active:scale-[0.97] disabled:opacity-50 [@media(hover:hover)]:hover:bg-active";
const kbd = "hidden rounded-md bg-card px-1.5 text-xs sm:inline font-medium text-muted-foreground";

/** The queue's count, shared by the nav badge and the Overview line. */
export function useReviewCount(): number {
  const { data } = useQuery({ queryKey: REVIEW_KEY, queryFn: fetchReviewQueue, refetchOnWindowFocus: true });
  return data?.remaining ?? 0;
}

function suggestions(groups: { name: string; subs: string[] }[]): Cat[] {
  const all: Cat[] = groups
    .filter((g) => g.name !== "Uncategorised")
    .flatMap((g) => g.subs.map((s): Cat => [g.name, s]));
  const seen = new Set<string>();
  return [...recent, ...all]
    .filter(([g, s]) => !seen.has(`${g}/${s}`) && seen.add(`${g}/${s}`))
    .slice(0, 3);
}

export function ReviewQueue() {
  const client = useQueryClient();
  const queue = useQuery({ queryKey: REVIEW_KEY, queryFn: fetchReviewQueue });
  const categories = useQuery({ queryKey: ["categories"], queryFn: fetchCategories });
  const [at, setAt] = useState(0);
  const [all, setAll] = useState(true); // "Apply to N similar" (the Merchant) or just this one
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);

  const act = useMutation({
    mutationFn: ({ call }: { call: () => Promise<ReviewQueueOut>; text: string }) => call(),
    onSuccess: (q, { text }) => {
      client.setQueryData(REVIEW_KEY, q);
      setNote({ text });
      setAll(true);
    },
    onError: (e) => setNote({ text: `${e.message} Showing the latest queue.`, bad: true }),
    // like an import: totals and lists everywhere may have changed
    onSettled: () => client.invalidateQueries(),
  });

  const items = queue.data?.items ?? [];
  const idx = Math.min(at, Math.max(items.length - 1, 0));
  const item: ReviewItem | undefined = items[idx];
  const groups = categories.data ?? [];
  const picks = item?.type === "category" ? suggestions(groups) : [];
  const many = (item?.weight ?? 0) > 1 && item?.transaction_ids.length;
  const justOne = !all && !!many;

  const pick = (cat: Cat) => {
    if (!item || act.isPending) return;
    recent.unshift(cat);
    recent.splice(8);
    const txn = justOne ? item.transaction_ids[0] : undefined;
    act.mutate({
      call: () => answerReview(item, { category: cat, ...(txn === undefined ? {} : { transaction_id: txn }) }),
      text: `${item.title}: ${cat[1]}${justOne ? ", just this one" : many ? `, ${item.weight} transactions` : ""}.`,
    });
  };
  const choose = (choice: string) => {
    if (!item || act.isPending) return;
    act.mutate({
      call: () => answerReview(item, { choice }),
      text: `${item.title || TYPE_LABEL[item.type] || "Item"}: ${CHOICE_LABEL[choice] ?? choice}.`,
    });
  };
  const skip = () => {
    if (!item || act.isPending) return;
    act.mutate({ call: () => skipReview(item), text: `Skipped ${item.title || "this item"}.` });
  };
  const undo = () => {
    if (!act.isPending) act.mutate({ call: undoReview, text: "Undid the last change." });
  };
  const move = (by: number) => setAt(Math.max(0, Math.min(items.length - 1, idx + by)));

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return;
      const k = e.key.toLowerCase();
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
  const title = item.title || label;
  return (
    <div className="mx-auto flex w-full max-w-xl flex-col gap-4">
      <div className="flex items-baseline justify-between gap-3 px-1">
        <p className="text-sm font-semibold">{items.length} to review</p>
        <p className="text-sm text-muted-foreground">
          {idx + 1} of {items.length}
        </p>
      </div>

      {status}

      <Card aria-labelledby="review-title" className="flex flex-col gap-5">
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

        {item.type === "category" && (
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
                  disabled={act.isPending}
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
                disabled={act.isPending}
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
          </>
        )}

        {item.type !== "category" && (
          <div className="grid gap-2 sm:grid-cols-2">
            {item.choices.map((c, i) => (
              <button
                key={c}
                type="button"
                aria-keyshortcuts={String(i + 1)}
                disabled={act.isPending}
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
        <button type="button" onClick={skip} disabled={act.isPending} aria-keyshortcuts="S" className={`${btn} glass`}>
          <SkipForward className="size-4" aria-hidden="true" />
          Skip
          <kbd className={kbd}>S</kbd>
        </button>
        <button type="button" onClick={undo} disabled={act.isPending} aria-keyshortcuts="U" className={`${btn} glass`}>
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
