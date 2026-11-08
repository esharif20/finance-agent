import type { components } from "@/api/schema";
import type { Money } from "@/lib/money";

type S = components["schemas"];
/** display_name (#210) is optional so an older server still renders: show `merchant` then. */
export type Entry = Omit<S["SubscriptionOut"], "display_name"> & { display_name?: string };
export type Kind = "subscription" | "bill";

export type FlagCode = "overlap" | "duplicate" | "charged_after_cancel" | "trial_converted" | "price_rise";
/** From #106 (PR #190). Kept optional over the generated schema so an older server still renders. */
export type Flag = { code: FlagCode; on: string | null; before: Money | null; after: Money | null };
export type Trial = { started_on: string; converts_on: string; converted: boolean };
export type IntervalSource = NonNullable<S["CostOut"]["interval_source"]>;

// #210's fields are optional too, for the same reason
// and #310's platform and likely total
type Since210 = "interval_source" | "charge_count" | "next_due_on" | "display_name" | "platform";
export type CostItem = Omit<S["CostOut"], "flags" | "trial" | Since210> & {
  flags?: Flag[];
  trial?: Trial | null;
} & Partial<Pick<S["CostOut"], Since210>>;
export type Costs = Omit<S["CostsOut"], "items" | "flag_count" | "trial_detection" | "total" | "likely_monthly"> & {
  likely_monthly?: Money;
  items: CostItem[];
  flag_count?: number;
  trial_detection?: "partial";
  total?: S["Total"];
};

export const GROUPS: Record<Kind, readonly string[]> = {
  subscription: ["Streaming", "Software", "AI tools", "Memberships", "Cloud storage", "Other subscriptions"],
  bill: ["Rent or mortgage", "Energy", "Water", "Council tax", "Phone & broadband", "Insurance", "Other bills"],
};
export const KIND_TITLE: Record<Kind, string> = { subscription: "Subscriptions", bill: "Bills" };

export type Row = {
  merchant_id: number;
  merchant: string; // as stored: Manage links match on it
  name: string; // what the screen shows
  status: string;
  monthly: Money | null;
  cost_note: string | null;
  counted: boolean;
  interval: string | null;
  interval_source: IntervalSource | null;
  charge_count: number | null;
  last_charge_on: string | null;
  next_due_on: string | null;
  entry: Entry;
  flags: Flag[];
  trial: Trial | null;
};

export const STATUSES = ["all", "active", "likely", "suggested", "overdue", "lapsed", "cancelled"] as const;
export type Filters = { status: (typeof STATUSES)[number]; kind: "all" | Kind; sort: "monthly" | "name" | "due" };
export const DEFAULT_FILTERS: Filters = { status: "all", kind: "all", sort: "monthly" };

export type BandName = "Active" | "Likely (new)" | "Needs a look" | "Ended";
/** Entries the list shows: listed by the user or detection, and guesses from a first charge (#310). */
export const LISTED = new Set(["active", "likely", "user_cancelled"]);
export type Band = { name: BandName; rows: Row[] };

const band = (r: Row): BandName =>
  r.status === "lapsed" || r.status === "cancelled"
    ? "Ended"
    : r.status === "likely"
      ? "Likely (new)"
      : r.status === "overdue" || r.flags.length > 0 || r.monthly === null
      ? "Needs a look"
      : "Active";

// unknowns sort last; Array.sort is stable, so ties keep the API's order
const last = <T,>(a: T | null, b: T | null, cmp: (x: T, y: T) => number) =>
  a === null ? (b === null ? 0 : 1) : b === null ? -1 : cmp(a, b);
const SORTS: Record<Filters["sort"], (a: Row, b: Row) => number> = {
  monthly: (a, b) => last(a.monthly?.minor ?? null, b.monthly?.minor ?? null, (x, y) => y - x),
  name: (a, b) => a.name.localeCompare(b.name, "en-GB", { sensitivity: "base" }),
  due: (a, b) => last(a.next_due_on, b.next_due_on, (x, y) => x.localeCompare(y)),
};

export function buildView(
  entries: Entry[],
  costs: Costs,
  f: Filters = DEFAULT_FILTERS,
): { bands: Band[]; suggested: Entry[] } {
  const cost = new Map(costs.items.map((c) => [c.merchant_id, c]));
  const kindOk = (k: string | null) => f.kind === "all" || k === f.kind;
  const suggested = entries.filter(
    (e) => e.status === "suggested" && (f.status === "all" || f.status === "suggested") && kindOk(e.kind),
  );
  const rows: Row[] = [];
  for (const entry of entries) {
    const c = cost.get(entry.merchant_id);
    if (!c || !entry.kind || !entry.group || !LISTED.has(entry.status)) continue;
    if (!kindOk(entry.kind) || (f.status !== "all" && c.status !== f.status)) continue;
    rows.push({
      merchant_id: c.merchant_id,
      merchant: c.merchant,
      name: c.display_name ?? entry.display_name ?? c.merchant,
      status: c.status,
      monthly: c.monthly,
      cost_note: c.cost_note,
      counted: c.counted,
      interval: c.interval,
      interval_source: c.interval_source ?? null,
      charge_count: c.charge_count ?? null,
      last_charge_on: c.last_charge_on,
      next_due_on: c.next_due_on ?? null,
      entry,
      flags: c.flags ?? [],
      trial: c.trial ?? null,
    });
  }
  rows.sort(SORTS[f.sort]);
  const bands = (["Active", "Likely (new)", "Needs a look", "Ended"] as const).map((name) => ({
    name,
    rows: rows.filter((r) => band(r) === name),
  }));
  return { bands: bands.filter((b) => b.rows.length), suggested };
}

const COST_NOTE: Record<string, string> = {
  rate_needed: "Needs an exchange rate",
  no_charge: "No charge seen yet",
};

/** The line under a row's cost: why it is an estimate, or why there is no cost yet. */
export function costText(r: Row): string | null {
  if (r.monthly) {
    if (r.interval_source === "estimated") return "Estimated from your charges";
    if (r.interval_source === "catalogue") return "Estimated: usual for this service";
    return null;
  }
  if (r.cost_note === "interval_needed")
    return r.charge_count === 1 ? "Only one charge so far" : "Charges too irregular to tell";
  return COST_NOTE[r.cost_note ?? ""] ?? "No cost yet";
}

/** The Overview's flag count: the API's own, else the items' flags, else null (not served yet). */
export function flagCount(costs: Costs): number | null {
  if (typeof costs.flag_count === "number") return costs.flag_count;
  if (costs.items.some((i) => i.flags)) return costs.items.reduce((n, i) => n + (i.flags?.length ?? 0), 0);
  return null;
}

export const dayText = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });

export function reasonText(e: Entry): string {
  const on = dayText(e.evidence.on);
  switch (e.reason) {
    case "known_service":
      return "Known service";
    case "user_confirmed":
      return `Confirmed by you on ${on}`;
    case "user_added":
      return `Added by you on ${on}`;
    case "pattern":
      return e.evidence.charge_count && e.evidence.interval
        ? `Detected: ${e.evidence.charge_count} ${e.evidence.interval} charges`
        : e.status === "likely"
          ? "Spotted from a first charge"
          : "Detected from a repeating pattern";
    default:
      return "";
  }
}
