import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { TriangleAlert } from "lucide-react";
import { useMemo, useState } from "react";
import {
  addSubscription,
  cancelSubscription,
  confirmSubscription,
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
  type Band,
  type Costs,
  DEFAULT_FILTERS,
  LISTED,
  type Filters,
  type Flag,
  KIND_TITLE,
  type Row,
  STATUSES,
  buildView,
  costText,
  dayText,
  flagCount,
  reasonText,
} from "@/lib/subscriptions";

const pill = "inline-flex min-h-8 items-center gap-1.5 rounded-full px-3 text-label font-semibold";
const action = `${pill} bg-active transition-[background-color,transform] duration-150 ease-out active:scale-[0.97] disabled:opacity-50 [@media(hover:hover)]:hover:bg-raised`;
const select = "min-h-11 max-w-full cursor-pointer rounded-full bg-active px-4 text-label font-semibold [@media(hover:hover)]:hover:bg-raised";

const STATUS: Record<string, string> = {
  active: "Active",
  likely: "Likely (new)",
  overdue: "Overdue",
  lapsed: "Lapsed",
  cancelled: "Cancelled by you",
};
const STATUS_OPTION: Record<Filters["status"], string> = {
  all: "All statuses",
  active: "Active",
  likely: "Likely (new)",
  suggested: "Suggested",
  overdue: "Overdue",
  lapsed: "Lapsed",
  cancelled: "Cancelled",
};
const SORT_OPTION: Record<Filters["sort"], string> = {
  monthly: "Dearest first",
  name: "Name, A to Z",
  due: "Next due",
};
const INTERVAL: Record<string, string> = { weekly: "Weekly", monthly: "Monthly", annual: "Yearly" };

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
    <ul className="mt-2 flex flex-wrap gap-2" aria-label={`Flags for ${row.name}`}>
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
    onSuccess: (_d, p) => onNote({ text: `${row.name} moved to ${p.group}.` }),
    onError: fail,
    onSettled: refresh,
  });
  const cancel = useMutation({
    mutationFn: () => cancelSubscription(row.merchant_id),
    onSuccess: () => onNote({ text: `${row.name} marked as cancelled. You'll be alerted if it charges again.` }),
    onError: fail,
    onSettled: refresh,
  });
  const remove = useMutation({
    mutationFn: () => removeSubscription(row.merchant_id),
    onSuccess: () =>
      onNote({
        text: `${row.name} removed. It's remembered as not a subscription.`,
        undo: () => addSubscription(row.merchant_id, place).then(refresh),
      }),
    onError: fail,
    onSettled: refresh,
  });
  const confirm = useMutation({
    mutationFn: () => confirmSubscription(row.merchant_id, place),
    onSuccess: () => onNote({ text: `${row.name} confirmed. It's in the monthly total now.` }),
    onError: fail,
    onSettled: refresh,
  });
  const busy = regroup.isPending || cancel.isPending || remove.isPending || confirm.isPending;
  const cancelled = row.status === "cancelled";
  const likely = row.status === "likely";
  const note = costText(row);
  const every = row.interval ? INTERVAL[row.interval] : null;
  const due =
    row.next_due_on && (row.status === "active" || row.status === "overdue" || likely)
      ? `${every ? `${every}, ` : ""}${row.status === "overdue" ? "was due" : likely ? "expected" : "next due"} ${dayText(row.next_due_on)}`
      : null;
  return (
    <li className="py-4 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">{row.name}</span>
            <span
              className={`${pill} min-h-6 ${row.status === "overdue" || row.status === "lapsed" ? "bg-warning text-warning-foreground" : "bg-active"}`}
            >
              {STATUS[row.status] ?? row.status}
            </span>
          </p>
          <p className="text-label text-muted-foreground">{reasonText(row.entry)}</p>
          {due && <p className="text-label text-muted-foreground">{due}</p>}
        </div>
        <p className="text-right">
          {row.monthly ? (
            <>
              <Money value={row.monthly} className={`text-heading tabular-nums ${row.counted ? "" : "text-muted-foreground"}`} />
              <span className="text-label text-muted-foreground"> a month</span>
              {note && <span className="block text-label text-muted-foreground">{note}</span>}
              {!row.counted && <span className="block text-label text-muted-foreground">Not in the total</span>}
            </>
          ) : (
            <span className="text-label text-muted-foreground">{note}</span>
          )}
        </p>
      </div>
      <Flags row={row} />
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <ManageLink merchant={row.merchant} name={row.name} />
        <PlacementSelect
          label={`Group for ${row.name}`}
          value={place}
          disabled={busy}
          onChange={(p) => regroup.mutate(p)}
        />
        {likely && (
          <button
            type="button"
            disabled={busy}
            onClick={() => confirm.mutate()}
            aria-label={`Yes, ${row.name} is a subscription`}
            className={`${pill} bg-primary text-primary-foreground transition-transform duration-150 ease-out active:scale-[0.97] disabled:opacity-50`}
          >
            Yes, it is
          </button>
        )}
        {!cancelled && !likely && (
          <button
            type="button"
            disabled={busy}
            onClick={() => cancel.mutate()}
            aria-label={`I cancelled ${row.name}`}
            className={action}
          >
            Cancelled?
          </button>
        )}
        <button type="button" disabled={busy} onClick={() => remove.mutate()} aria-label={likely ? `${row.name} is not a subscription` : `Remove ${row.name}`}
          className={action}
        >
          {likely ? "Not a subscription" : "Remove"}
        </button>
      </div>
    </li>
  );
}

type Note = { text: string; bad?: boolean; undo?: () => Promise<unknown> };

const BAND_HINT: Record<Band["name"], string> = {
  Active: "Charging as expected.",
  "Likely (new)": "Spotted from a first charge. Not in the total until you confirm it or it charges again.",
  "Needs a look": "Overdue, flagged, or without a cost yet.",
  Ended: "Lapsed or cancelled. Not in the total.",
};

function BandCard({ band, onNote }: { band: Band; onNote: (n: Note) => void }) {
  const id = `band-${band.name.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <Card aria-labelledby={id}>
      <div className="mb-5 flex flex-wrap items-baseline justify-between gap-x-4">
        <h2 id={id} className="text-title">
          {band.name}
        </h2>
        <p className="text-label text-muted-foreground">
          {band.rows.length} {band.rows.length === 1 ? "entry" : "entries"}. {BAND_HINT[band.name]}
        </p>
      </div>
      <ul className="divide-y divide-foreground/10">
        {band.rows.map((r) => (
          <Item key={r.merchant_id} row={r} onNote={onNote} />
        ))}
      </ul>
    </Card>
  );
}

/** The API's totals for counted items: never summed here. */
function Summary({ costs }: { costs: Costs }) {
  const kinds = costs.totals.filter((t): t is typeof t & { kind: keyof typeof KIND_TITLE } => t.kind in KIND_TITLE);
  if (!costs.total) return null;
  return (
    <Card aria-labelledby="sub-total">
      <h2 id="sub-total" className="text-heading text-muted-foreground">
        Active subscriptions and bills
      </h2>
      <div className="mt-2 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-3">
        <p>
          <Money value={costs.total.monthly} className="text-hero tabular-nums" />
          <span className="text-muted-foreground"> a month</span>
          <span className="block text-muted-foreground">
            <Money value={costs.total.annual} className="tabular-nums text-foreground" /> a year
          </span>
        </p>
        {(kinds.length > 0 || !!costs.likely_monthly?.minor) && (
          <dl className="flex flex-wrap gap-x-6 gap-y-1">
            {kinds.map((t) => (
              <div key={t.kind}>
                <dt className="text-label text-muted-foreground">{KIND_TITLE[t.kind]}</dt>
                <dd>
                  <Money value={t.monthly} className="text-heading tabular-nums" />
                  <span className="text-label text-muted-foreground"> a month</span>
                </dd>
              </div>
            ))}
            {costs.likely_monthly?.minor ? (
              <div>
                <dt className="text-label text-muted-foreground">Likely (new), not in the total</dt>
                <dd>
                  <Money value={costs.likely_monthly} className="text-heading tabular-nums text-muted-foreground" />
                  <span className="text-label text-muted-foreground"> a month</span>
                </dd>
              </div>
            ) : null}
          </dl>
        )}
      </div>
    </Card>
  );
}

function FilterBar({ value, onChange }: { value: Filters; onChange: (f: Filters) => void }) {
  return (
    <div role="group" aria-label="Filter and sort" className="flex flex-wrap items-center gap-2">
      <select
        aria-label="Status"
        value={value.status}
        onChange={(e) => onChange({ ...value, status: e.target.value as Filters["status"] })}
        className={select}
      >
        {STATUSES.map((s) => (
          <option key={s} value={s}>
            {STATUS_OPTION[s]}
          </option>
        ))}
      </select>
      <select
        aria-label="Kind"
        value={value.kind}
        onChange={(e) => onChange({ ...value, kind: e.target.value as Filters["kind"] })}
        className={select}
      >
        <option value="all">Subscriptions and bills</option>
        <option value="subscription">Subscriptions only</option>
        <option value="bill">Bills only</option>
      </select>
      <select
        aria-label="Sort by"
        value={value.sort}
        onChange={(e) => onChange({ ...value, sort: e.target.value as Filters["sort"] })}
        className={select}
      >
        {(Object.keys(SORT_OPTION) as Filters["sort"][]).map((s) => (
          <option key={s} value={s}>
            {SORT_OPTION[s]}
          </option>
        ))}
      </select>
    </div>
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
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const view = useMemo(
    () => (entries.data && costs.data ? buildView(entries.data, costs.data, filters) : null),
    [entries.data, costs.data, filters],
  );
  const listed = useMemo(
    () => (entries.data ?? []).some((e) => LISTED.has(e.status)),
    [entries.data],
  );
  const filtered = filters.status !== "all" || filters.kind !== "all";
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
      {listed && <Summary costs={costs.data} />}
      {listed && <FilterBar value={filters} onChange={setFilters} />}
      <SubscriptionPrompt suggested={view.suggested} />
      {!listed ? (
        <Card>
          <h2 className="text-heading">Nothing on your list yet</h2>
          <p className="mt-2 text-muted-foreground">
            Known services appear after their first charge. Anything that repeats gets a question here, or add one yourself.
          </p>
        </Card>
      ) : view.bands.length === 0 && view.suggested.length === 0 ? (
        <Card>
          <h2 className="text-heading">Nothing matches these filters</h2>
          {filtered && (
            <button type="button" onClick={() => setFilters(DEFAULT_FILTERS)} className={`${action} mt-3 min-h-11 px-5 text-sm`}>
              Show everything
            </button>
          )}
        </Card>
      ) : (
        view.bands.map((b) => <BandCard key={b.name} band={b} onNote={setNote} />)
      )}
      {costs.data.trial_detection === "partial" && (
        <p className="text-label text-muted-foreground">
          Free trial detection is partial: a trial that starts free stays unseen until email signals arrive.
        </p>
      )}
    </div>
  );
}
