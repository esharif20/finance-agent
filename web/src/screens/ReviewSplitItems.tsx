import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { type CategoryGroup, type ReviewItem, fetchSplit, fetchTransaction } from "@/api/client";
import { Money } from "@/components/Money";
import { NoteField } from "@/components/NoteField";
import { SplitEditor } from "@/components/SplitEditor";

const btn =
  "inline-flex min-h-11 items-center justify-center gap-2 rounded-full px-4 text-sm font-semibold transition-[background-color,transform] duration-150 ease-out active:scale-[0.97] disabled:opacity-50 [@media(hover:hover)]:hover:bg-active";

type Cat = [string, string];
type Body = { item: ReviewItem; groups: CategoryGroup[]; busy: boolean };

/** "What was this?": besides picking a Category, you can split the payment or leave a note. */
export function AlwaysAskExtras({ item, groups }: Pick<Body, "item" | "groups">) {
  const [mode, setMode] = useState<"split" | "note" | null>(null);
  const txn = item.transaction_ids[0] ?? 0;
  const found = useQuery({
    queryKey: ["review-txn", txn],
    queryFn: () => fetchTransaction(txn),
    enabled: mode === "split",
  });
  const toggle = (m: "split" | "note") => setMode((cur) => (cur === m ? null : m));

  return (
    <div className="flex flex-col gap-3">
      <div role="group" aria-label="Or" className="flex flex-wrap gap-2">
        {(["split", "note"] as const).map((m) => (
          <button key={m} type="button" aria-pressed={mode === m} onClick={() => toggle(m)} className={`${btn} ${mode === m ? "bg-active" : "bg-raised"}`}>
            {m === "split" ? "Split it" : "Add a note"}
          </button>
        ))}
      </div>
      {mode === "note" && <NoteField txn={txn} />}
      {mode === "split" &&
        (found.isPending ? (
          <p className="text-sm text-muted-foreground">Finding the payment…</p>
        ) : found.data ? (
          <SplitEditor
            txn={txn}
            total={found.data.amount.minor}
            currency={found.data.amount.currency}
            groups={groups}
            onDone={() => setMode(null)}
          />
        ) : (
          <p className="text-sm text-muted-foreground">
            Couldn't find this payment here. Open it in <a href="#transactions" className="underline underline-offset-4">Transactions</a> to split it.
          </p>
        ))}
    </div>
  );
}

/** A split payment whose amount changed: keep the old parts counted whole, re-split, or undo the split. */
export function SplitRecheck({ item, groups, busy, choose }: Body & { choose: (choice: string) => void }) {
  const txn = item.transaction_ids[0] ?? 0;
  const [resplit, setResplit] = useState(false);
  const split = useQuery({ queryKey: ["split", txn], queryFn: () => fetchSplit(txn) });
  const s = split.data;
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        This payment changed after you split it, so the parts no longer add up. Nothing was rescaled. Until you decide, the whole
        payment counts under its largest part.
      </p>
      {s && (
        <ol aria-label="Current parts" className="flex flex-col rounded-[14px] bg-raised px-4 py-2">
          {s.parts.map((p, i) => (
            <li key={i} className="flex min-h-11 items-center justify-between gap-3 text-row">
              <span className="truncate font-semibold">{p.category[1]}</span>
              <Money value={{ minor: p.amount_minor, currency: s.currency }} sign />
            </li>
          ))}
          <li className="flex min-h-11 items-center justify-between gap-3 border-t border-[var(--control)] text-row">
            <span className="text-muted-foreground">Payment now</span>
            <Money value={{ minor: s.amount_minor, currency: s.currency }} sign className="font-semibold" />
          </li>
        </ol>
      )}
      {resplit && s ? (
        <SplitEditor txn={txn} total={s.amount_minor} currency={s.currency} groups={groups} initial={s.parts} onDone={() => setResplit(false)} />
      ) : (
        <div className="grid gap-2 sm:grid-cols-2">
          <button type="button" disabled={busy || !s} onClick={() => setResplit(true)} className={`${btn} min-h-14 bg-raised`}>
            Re-split
          </button>
          <button type="button" disabled={busy} aria-keyshortcuts="1" onClick={() => choose("revert")} className={`${btn} min-h-14 bg-raised`}>
            Undo the split
          </button>
        </div>
      )}
    </div>
  );
}

/** "Which part?": a refund of a split payment. Choose the part it was for. */
export function SplitRefund({ item, busy, pick }: Body & { pick: (cat: Cat) => void }) {
  const purchase = item.transaction_ids[1] ?? 0;
  const split = useQuery({ queryKey: ["split", purchase], queryFn: () => fetchSplit(purchase) });
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        This refund was matched to a split payment at {item.display_name || "the same shop"}. Which part was it for? Until you choose, it
        comes off the largest part. If it belongs to a different purchase, skip it.
      </p>
      {split.isPending ? (
        <p className="text-sm text-muted-foreground">Loading the parts…</p>
      ) : !split.data ? (
        <p className="text-sm">That payment is no longer split. Skip this one.</p>
      ) : (
        <div role="group" aria-label="Which part" className="grid gap-2 sm:grid-cols-2">
          {split.data.parts.map((p, i) => (
            <button
              key={i}
              type="button"
              disabled={busy}
              title={p.category[0]}
              onClick={() => pick([p.category[0] ?? "", p.category[1] ?? ""])}
              className={`${btn} min-h-14 justify-between bg-raised`}
            >
              {p.category[1]}
              <Money value={{ minor: Math.abs(p.amount_minor), currency: split.data!.currency }} className="font-medium text-muted-foreground" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
