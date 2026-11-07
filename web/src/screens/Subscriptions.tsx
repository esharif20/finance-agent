import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { TriangleAlert } from "lucide-react";
import { useMemo, useState } from "react";
import {
  addSubscription,
  cancelSubscription,
  fetchMerchants,
  fetchSubscriptionCosts,
  fetchSubscriptions,
  regroupSubscription,
  removeSubscription,
} from "@/api/client";
import { ManageLink } from "@/components/ManageLink";
import { Money } from "@/components/Money";
import { PlacementSelect, type Place } from "@/components/PlacementSelect";
import { SUBS_KEY, SubscriptionPrompt } from "@/components/SubscriptionPrompt";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type Flag,
  type Row,
  type Section,
  buildView,
  dayText,
  flagCount,
  reasonText,
} from "@/lib/subscriptions";

const pill = "inline-flex min-h-8 items-center gap-1.5 rounded-full px-3 text-label font-semibold";
const action = `${pill} bg-active transition-[background-color,transform] duration-150 ease-out active:scale-[0.97] disabled:opacity-50 [@media(hover:hover)]:hover:bg-raised`;

const STATUS: Record<string, string> = {
  active: "Active",
  overdue: "Overdue",
  lapsed: "Lapsed",
  cancelled: "Cancelled by you",
};
const COST_NOTE: Record<string, string> = {
  rate_needed: "Needs an exchange rate",
  interval_needed: "Interval not known yet",
  no_charge: "No charge seen yet",
};

function flagText(f: Flag, group: string): React.ReactNode {
  switch (f.code) {
    case "overlap":
      return `Overlaps with another subscription in ${group}`;
    case "duplicate":
      return "Charged from two accounts";
    case "charged_after_cancel":
      return `Charged after you cancelled${f.on ? ` (${dayText(f.on)})` : ""}`;
    case "trial_converted":
      return "Free trial turned into a paid plan";
    case "price_rise":
      return f.before && f.after ? (
        <>
          Price rose from <Money value={f.before} /> to <Money value={f.after} />
        </>
      ) : (
        "Price rose"
      );
  }
}

function Flags({ row }: { row: Row }) {
  const trialOpen = row.trial && !row.trial.converted;
  if (!row.flags.length && !trialOpen) return null;
  return (
    <ul className="mt-2 flex flex-wrap gap-2" aria-label={`Flags for ${row.merchant}`}>
      {row.flags.map((f) => (
        <li key={f.code} className={`${pill} bg-warning text-warning-foreground`}>
          <TriangleAlert className="size-3.5 shrink-0" aria-hidden="true" />
          <span>{flagText(f, row.entry.group ?? "")}</span>
        </li>
      ))}
      {trialOpen && row.trial && (
        <li className={`${pill} bg-active`}>Free trial, converts {dayText(row.trial.converts_on)}</li>
      )}
    </ul>
  );
}

function Item({ row, onNote }: { row: Row; onNote: (n: Note) => void }) {
  const client = useQueryClient();
  const kind = row.entry.kind === "bill" ? "bill" : "subscription";
  const place: Place = { kind, group: row.entry.group ?? "" };
  const refresh = () => client.invalidateQueries({ queryKey: SUBS_KEY });
  const fail = (e: Error) => onNote({ text: e.message, bad: true });
  const regroup = useMutation({
    mutationFn: (p: Place) => regroupSubscription(row.merchant_id, p),
    onSuccess: (_d, p) => onNote({ text: `${row.merchant} moved to ${p.group}.` }),
    onError: fail,
    onSettled: refresh,
  });
  const cancel = useMutation({
    mutationFn: () => cancelSubscription(row.merchant_id),
    onSuccess: () => onNote({ text: `${row.merchant} marked as cancelled. You'll be alerted if it charges again.` }),
    onError: fail,
    onSettled: refresh,
  });
  const remove = useMutation({
    mutationFn: () => removeSubscription(row.merchant_id),
    onSuccess: () =>
      onNote({
        text: `${row.merchant} removed. It's remembered as not a subscription.`,
        undo: () => addSubscription(row.merchant_id, place).then(refresh),
      }),
    onError: fail,
    onSettled: refresh,
  });
  const busy = regroup.isPending || cancel.isPending || remove.isPending;
  const cancelled = row.status === "cancelled";
  return (
    <li className="py-4 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">{row.merchant}</span>
            <span
              className={`${pill} min-h-6 ${row.status === "overdue" || row.status === "lapsed" ? "bg-warning text-warning-foreground" : "bg-active"}`}
            >
              {STATUS[row.status] ?? row.status}
            </span>
          </p>
          <p className="text-label text-muted-foreground">{reasonText(row.entry)}</p>
        </div>
        <p className="text-right">
          {row.monthly ? (
            <>
              <Money value={row.monthly} className={`text-heading tabular-nums ${row.counted ? "" : "text-muted-foreground"}`} />
              <span className="text-label text-muted-foreground"> a month</span>
              {!row.counted && <span className="block text-label text-muted-foreground">Not in the total</span>}
            </>
          ) : (
            <span className="text-label text-muted-foreground">{COST_NOTE[row.cost_note ?? ""] ?? "No cost yet"}</span>
          )}
        </p>
      </div>
      <Flags row={row} />
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <ManageLink merchant={row.merchant} />
        <PlacementSelect
          label={`Group for ${row.merchant}`}
          value={place}
          disabled={busy}
          onChange={(p) => regroup.mutate(p)}
        />
        {!cancelled && (
          <button
            type="button"
            disabled={busy}
            onClick={() => cancel.mutate()}
            aria-label={`I cancelled ${row.merchant}`}
            className={action}
          >
            Cancelled?
          </button>
        )}
        <button type="button" disabled={busy} onClick={() => remove.mutate()} aria-label={`Remove ${row.merchant}`} className={action}>
          Remove
        </button>
      </div>
    </li>
  );
}

type Note = { text: string; bad?: boolean; undo?: () => Promise<unknown> };

function SectionCard({ section, onNote }: { section: Section; onNote: (n: Note) => void }) {
  const id = `sub-${section.kind}`;
  return (
    <Card aria-labelledby={id}>
      <div className="mb-5 flex flex-wrap items-baseline justify-between gap-x-4">
        <h2 id={id} className="text-title">
          {section.title}
        </h2>
        <p>
          <Money value={section.total} className="text-title tabular-nums" />
          <span className="text-label text-muted-foreground"> a month</span>
        </p>
      </div>
      <div className="flex flex-col gap-6">
        {section.groups.map((g) => (
          <section key={g.name} aria-label={`${g.name}, ${section.title}`}>
            <div className="mb-3 flex items-baseline justify-between gap-3">
              <h3 className="text-heading">{g.name}</h3>
              <p className="text-label text-muted-foreground">
                <Money value={g.total} /> a month
              </p>
            </div>
            <ul className="divide-y divide-foreground/10">
              {g.rows.map((r) => (
                <Item key={r.merchant_id} row={r} onNote={onNote} />
              ))}
            </ul>
          </section>
        ))}
      </div>
    </Card>
  );
}

function AddOne({ taken, onNote }: { taken: Set<number>; onNote: (n: Note) => void }) {
  const client = useQueryClient();
  const merchants = useQuery({ queryKey: ["merchants"], queryFn: fetchMerchants });
  const [open, setOpen] = useState(false);
  const [merchant, setMerchant] = useState("");
  const [place, setPlace] = useState<Place>({ kind: "subscription", group: "Other subscriptions" });
  const options = (merchants.data ?? []).filter((m) => !taken.has(m.id));
  const add = useMutation({
    mutationFn: () => addSubscription(Number(merchant), place),
    onSuccess: () => {
      onNote({ text: "Added to your list." });
      setMerchant("");
      setOpen(false);
    },
    onError: (e) => onNote({ text: e.message, bad: true }),
    onSettled: () => client.invalidateQueries({ queryKey: SUBS_KEY }),
  });
  if (!open)
    return (
      <button type="button" onClick={() => setOpen(true)} className={`${action} min-h-11 px-5 text-sm`}>
        Add one
      </button>
    );
  return (
    <form
      className="flex flex-wrap items-center gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (merchant) add.mutate();
      }}
    >
      <select
        aria-label="Merchant to add"
        value={merchant}
        onChange={(e) => setMerchant(e.target.value)}
        className="min-h-9 max-w-full rounded-full bg-active px-3 text-label font-semibold"
      >
        <option value="">Choose a merchant</option>
        {options.map((m) => (
          <option key={m.id} value={m.id}>
            {m.name}
          </option>
        ))}
      </select>
      <PlacementSelect label="Group for the new entry" value={place} onChange={setPlace} />
      <button type="submit" disabled={!merchant || add.isPending} className={`${action} min-h-11 bg-primary px-5 text-sm text-primary-foreground`}>
        Add
      </button>
      <button type="button" onClick={() => setOpen(false)} className={`${action} min-h-11 px-5 text-sm`}>
        Cancel
      </button>
    </form>
  );
}

/** Subscriptions and Bills: what recurs, what it costs a month, what may be wasting money. */
export function Subscriptions() {
  const entries = useQuery({ queryKey: [...SUBS_KEY, "entries"], queryFn: fetchSubscriptions });
  const costs = useQuery({ queryKey: [...SUBS_KEY, "costs"], queryFn: fetchSubscriptionCosts });
  const [note, setNote] = useState<Note | null>(null);
  const view = useMemo(
    () => (entries.data && costs.data ? buildView(entries.data, costs.data) : null),
    [entries.data, costs.data],
  );
  const taken = useMemo(
    () => new Set((entries.data ?? []).filter((e) => e.status !== "not_subscription").map((e) => e.merchant_id)),
    [entries.data],
  );

  if (entries.error || costs.error)
    return (
      <Card role="alert">
        <h2 className="text-heading">Can't load your subscriptions</h2>
        <p className="mt-2 text-muted-foreground">Check that the local server is running, then try again.</p>
        <button
          type="button"
          onClick={() => {
            entries.refetch();
            costs.refetch();
          }}
          className="mt-4 min-h-11 rounded-full bg-primary px-5 text-sm font-semibold text-primary-foreground"
        >
          Try again
        </button>
      </Card>
    );
  if (!view || !costs.data)
    return (
      <div className="flex flex-col gap-6" aria-busy="true">
        <Skeleton className="h-[220px] rounded-card" />
        <Skeleton className="h-[220px] rounded-card" />
      </div>
    );

  const flags = flagCount(costs.data);
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-muted-foreground">
          {flags === null
            ? "What recurs and what it costs each month."
            : `${flags} ${flags === 1 ? "flag" : "flags"} on your list.`}
          {costs.data.rate_needed > 0 && " Some entries wait for an exchange rate and are left out of the totals."}
        </p>
        <AddOne taken={taken} onNote={setNote} />
      </div>
      <div role="status" className="empty:hidden">
        {note && (
          <p className={`flex flex-wrap items-center gap-3 text-label ${note.bad ? "text-warning-foreground" : "text-muted-foreground"}`}>
            {note.text}
            {note.undo && (
              <button
                type="button"
                onClick={() => note.undo?.().then(() => setNote({ text: "Put back." })).catch((e: Error) => setNote({ text: e.message, bad: true }))}
                className={action}
              >
                Undo
              </button>
            )}
          </p>
        )}
      </div>
      <SubscriptionPrompt suggested={view.suggested} />
      {view.sections.length === 0 ? (
        <Card>
          <h2 className="text-heading">Nothing on your list yet</h2>
          <p className="mt-2 text-muted-foreground">
            Known services appear after their first charge. Anything that repeats gets a question here, or add one yourself.
          </p>
        </Card>
      ) : (
        view.sections.map((s) => <SectionCard key={s.kind} section={s} onNote={setNote} />)
      )}
      {costs.data.trial_detection === "partial" && (
        <p className="text-label text-muted-foreground">
          Free trial detection is partial: a trial that starts free stays unseen until email signals arrive.
        </p>
      )}
    </div>
  );
}
