import { ChevronDown } from "lucide-react";
import { useId, useState } from "react";
import type { CategoryGroup, SplitOut, Txn } from "@/api/client";
import { AlwaysAskToggle } from "@/components/AlwaysAskToggle";
import { CategoryChip, type Category } from "@/components/CategoryChip";
import { ManageLink } from "@/components/ManageLink";
import { Money } from "@/components/Money";
import { NoteField } from "@/components/NoteField";
import { SplitBadge, SplitParts } from "@/components/SplitBadge";
import { SplitEditor } from "@/components/SplitEditor";
import { isSubscription } from "@/lib/manageLinks";
import { addsDetail, joinParts } from "@/lib/txnText";

export const day = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });

const categorised = (t: Txn) => t.kind === "Spending" || t.kind === "Refund";

/** One Transaction: its Category chip (or the Split badge), and a details panel for the
 * note, the Split editor and the Merchant's "Always ask" switch. */
export function TxnRow({
  t,
  mask,
  groups,
  onCategory,
}: {
  t: Txn;
  mask?: string;
  groups: CategoryGroup[];
  onCategory: (c: Category, all: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<{ parts?: SplitOut["parts"] } | null>(null);
  const [partsOpen, setPartsOpen] = useState(false);
  const panel = useId();
  const partsId = useId();
  const isSplit = t.source === "split";
  const secondary = joinParts([t.merchant && addsDetail(t.merchant, t.description) ? t.description : null, mask]);
  const toReview = t.category?.[0] === "To review";

  return (
    <li
      aria-label={`${t.description}, ${day(t.posted_on)}`}
      className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1.5 rounded-[12px] px-3 py-3 hover:bg-raised sm:grid-cols-[96px_1fr_auto_auto]"
    >
      <time dateTime={t.posted_on} className="order-3 text-label text-muted-foreground sm:order-none">
        {day(t.posted_on)}
      </time>
      <div className="order-1 min-w-0 sm:order-none">
        <p className="truncate text-row font-semibold">{t.merchant ?? t.description}</p>
        {secondary && <p className="truncate text-label font-normal text-muted-foreground">{secondary}</p>}
      </div>
      <div className="order-2 text-right sm:order-none sm:min-w-28">
        <Money
          value={t.amount}
          sign
          className={`text-row font-semibold tracking-[-0.01em] ${t.amount.minor > 0 ? "text-positive" : ""}`}
        />
      </div>
      <div className="order-4 flex items-start justify-self-end sm:order-none sm:min-w-44 sm:justify-self-start">
        <div className="min-w-0">
          {isSplit ? (
            <SplitBadge txn={t.id} open={partsOpen} controls={partsId} onToggle={() => setPartsOpen((o) => !o)} />
          ) : categorised(t) ? (
            <CategoryChip value={t.category as Category | null} groups={groups} merchant={t.merchant} onChange={onCategory} />
          ) : (
            <span className="text-label text-muted-foreground">{t.kind}</span>
          )}
          {toReview && <p className="mt-1 text-meta text-muted-foreground">Waiting for your answer</p>}
          {isSubscription(t.category) && (
            <div className="mt-1.5">
              <ManageLink merchant={t.merchant} />
            </div>
          )}
        </div>
        {categorised(t) && (
          <button
            type="button"
            aria-expanded={open}
            aria-controls={panel}
            aria-label={`Details for ${t.merchant ?? t.description}`}
            onClick={() => setOpen((o) => !o)}
            className="ml-1 inline-flex size-11 shrink-0 items-center justify-center rounded-full [@media(hover:hover)]:hover:bg-active"
          >
            <ChevronDown className={`size-4 transition-transform duration-200 ease-out motion-reduce:transition-none ${open ? "rotate-180" : ""}`} aria-hidden="true" />
          </button>
        )}
      </div>

      {isSplit && (
        <SplitParts
          txn={t.id}
          id={partsId}
          open={partsOpen}
          onClose={() => setPartsOpen(false)}
          onResplit={(s) => {
            setEditing({ parts: s.parts });
            setOpen(true);
          }}
        />
      )}
      {open && categorised(t) && (
        <div id={panel} className="order-5 col-span-full mt-1 flex flex-col gap-4 rounded-[14px] bg-card p-4 sm:order-none">
          <NoteField txn={t.id} />
          {editing ? (
            <SplitEditor
              txn={t.id}
              total={t.amount.minor}
              currency={t.amount.currency}
              groups={groups}
              initial={editing.parts}
              onDone={() => setEditing(null)}
            />
          ) : (
            !isSplit && (
              <div>
                <button
                  type="button"
                  onClick={() => setEditing({})}
                  className="min-h-11 rounded-full bg-active px-4 text-sm font-semibold transition-transform duration-150 ease-out active:scale-[0.97]"
                >
                  Split this payment
                </button>
              </div>
            )
          )}
          {t.merchant_id !== null && t.merchant && <AlwaysAskToggle merchantId={t.merchant_id} name={t.merchant} />}
        </div>
      )}
    </li>
  );
}
