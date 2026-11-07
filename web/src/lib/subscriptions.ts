import type { components } from "@/api/schema";
import type { Money } from "@/lib/money";

type S = components["schemas"];
export type Entry = S["SubscriptionOut"];
export type Kind = "subscription" | "bill";

export type FlagCode = "overlap" | "duplicate" | "charged_after_cancel" | "trial_converted" | "price_rise";
/** From PR #190 (#106). Not in the generated schema until it merges, so every use treats it as optional. */
export type Flag = { code: FlagCode; on: string | null; before: Money | null; after: Money | null };
export type Trial = { started_on: string; converts_on: string; converted: boolean };

export type CostItem = S["CostOut"] & { flags?: Flag[]; trial?: Trial | null };
export type Costs = Omit<S["CostsOut"], "items"> & {
  items: CostItem[];
  flag_count?: number;
  trial_detection?: "partial";
};

export const GROUPS: Record<Kind, readonly string[]> = {
  subscription: ["Streaming", "Software", "AI tools", "Memberships", "Cloud storage", "Other subscriptions"],
  bill: ["Rent or mortgage", "Energy", "Water", "Council tax", "Phone & broadband", "Insurance", "Other bills"],
};
export const KIND_TITLE: Record<Kind, string> = { subscription: "Subscriptions", bill: "Bills" };

export type Row = {
  merchant_id: number;
  merchant: string;
  status: string;
  monthly: Money | null;
  cost_note: string | null;
  counted: boolean;
  interval: string | null;
  last_charge_on: string | null;
  entry: Entry;
  flags: Flag[];
  trial: Trial | null;
};
export type Group = { name: string; total: Money; rows: Row[] };
export type Section = { kind: Kind; title: string; total: Money; groups: Group[] };

const zero: Money = { minor: 0, currency: "GBP" };

export function buildView(entries: Entry[], costs: Costs): { sections: Section[]; suggested: Entry[] } {
  const cost = new Map(costs.items.map((c) => [c.merchant_id, c]));
  const suggested = entries.filter((e) => e.status === "suggested");
  const rows = new Map<string, Row[]>();
  for (const entry of entries) {
    const c = cost.get(entry.merchant_id);
    if (!c || !entry.kind || !entry.group || (entry.status !== "active" && entry.status !== "user_cancelled")) continue;
    const key = `${entry.kind}|${entry.group}`;
    rows.set(key, [
      ...(rows.get(key) ?? []),
      {
        merchant_id: c.merchant_id,
        merchant: c.merchant,
        status: c.status,
        monthly: c.monthly,
        cost_note: c.cost_note,
        counted: c.counted,
        interval: c.interval,
        last_charge_on: c.last_charge_on,
        entry,
        flags: c.flags ?? [],
        trial: c.trial ?? null,
      },
    ]);
  }
  const sections: Section[] = [];
  for (const kind of ["subscription", "bill"] as const) {
    const extra = [...rows.keys()].filter((k) => k.startsWith(`${kind}|`)).map((k) => k.slice(kind.length + 1));
    const groups: Group[] = [...new Set([...GROUPS[kind], ...extra])].flatMap((name) => {
      const list = rows.get(`${kind}|${name}`);
      if (!list) return [];
      // Array.sort is stable: counted rows first, the rest after
      const sorted = [...list].sort((a, b) => Number(b.counted) - Number(a.counted));
      const total = costs.groups.find((g) => g.kind === kind && g.group === name)?.monthly ?? zero;
      return [{ name, total, rows: sorted }];
    });
    if (groups.length)
      sections.push({
        kind,
        title: KIND_TITLE[kind],
        total: costs.totals.find((t) => t.kind === kind)?.monthly ?? zero,
        groups,
      });
  }
  return { sections, suggested };
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
        : "Detected from a repeating pattern";
    default:
      return "";
  }
}
