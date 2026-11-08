import { describe, expect, it } from "vitest";
import {
  DEFAULT_FILTERS,
  buildView,
  costText,
  flagCount,
  reasonText,
  type CostItem,
  type Entry,
  type Filters,
} from "./subscriptions";

// synthetic
const p = (minor: number) => ({ minor, currency: "GBP" });
const entry = (id: number, over: Partial<Entry> = {}): Entry => ({
  merchant_id: id,
  merchant: `Svc ${id}`,
  kind: "subscription",
  group: "Streaming",
  status: "active",
  reason: "known_service",
  source: "detection",
  evidence: { on: "2026-03-01", charge_count: 3, interval: "monthly" },
  created_at: "2026-03-01T00:00:00",
  changed_at: "2026-03-01T00:00:00",
  ...over,
});
const cost = (id: number, over: Partial<CostItem> = {}): CostItem => ({
  merchant_id: id,
  merchant: `Svc ${id}`,
  kind: "subscription",
  group: "Streaming",
  status: "active",
  interval: "monthly",
  last_charge_on: "2026-03-01",
  monthly: p(1000),
  cost_note: null,
  counted: true,
  ...over,
});
const costs = (items: CostItem[], over = {}) => ({
  items,
  totals: [
    { kind: "subscription", monthly: p(2500) },
    { kind: "bill", monthly: p(9000) },
  ],
  groups: [],
  rate_needed: 0,
  ...over,
});
const view = (e: Entry[], c: CostItem[], f: Partial<Filters> = {}) =>
  buildView(e, costs(c), { ...DEFAULT_FILTERS, ...f });
const names = (v: ReturnType<typeof view>) =>
  Object.fromEntries(v.bands.map((b) => [b.name, b.rows.map((r) => r.merchant_id)]));

describe("buildView", () => {
  it("bands rows as Active, Needs a look and Ended, dropping empty bands", () => {
    const f = { code: "overlap" as const, on: null, before: null, after: null };
    const e = [1, 2, 3, 4, 5, 6].map((i) => entry(i, i === 5 ? { status: "user_cancelled" } : {}));
    const c = [
      cost(1),
      cost(2, { status: "overdue" }),
      cost(3, { flags: [f] }),
      cost(4, { status: "lapsed", counted: false }),
      cost(5, { status: "cancelled", counted: false }),
      cost(6, { monthly: null, cost_note: "interval_needed", counted: false }),
    ];
    expect(names(view(e, c))).toEqual({ Active: [1], "Needs a look": [2, 3, 6], Ended: [4, 5] });
    expect(view([entry(1)], [cost(1)]).bands.map((b) => b.name)).toEqual(["Active"]);
  });

  it("keeps suggestions and removed entries out of the bands", () => {
    const e = [
      entry(1, { status: "suggested", reason: "pattern" }),
      entry(2, { status: "not_subscription", kind: null, group: null }),
      entry(3),
    ];
    const v = view(e, [cost(1, { status: "suggested", counted: false }), cost(3)]);
    expect(v.suggested.map((s) => s.merchant_id)).toEqual([1]);
    expect(names(v)).toEqual({ Active: [3] });
  });

  it("filters by status and kind; suggestions show only for all or suggested", () => {
    const e = [entry(1), entry(2, { kind: "bill", group: "Energy" }), entry(3, { status: "suggested", kind: "bill" })];
    const c = [cost(1), cost(2, { kind: "bill", group: "Energy", status: "overdue" }), cost(3, { status: "suggested" })];
    expect(names(view(e, c, { status: "overdue" }))).toEqual({ "Needs a look": [2] });
    expect(view(e, c, { status: "overdue" }).suggested).toEqual([]);
    expect(names(view(e, c, { kind: "subscription" }))).toEqual({ Active: [1] });
    expect(view(e, c, { kind: "subscription" }).suggested).toEqual([]);
    const s = view(e, c, { status: "suggested", kind: "bill" });
    expect([s.bands, s.suggested.map((x) => x.merchant_id)]).toEqual([[], [3]]);
  });

  it("sorts by monthly cost (dearest first), name, or next due, unknowns last", () => {
    const e = [entry(1, { merchant: "b" }), entry(2, { merchant: "a" }), entry(3, { merchant: "c" })];
    const c = [
      cost(1, { merchant: "b", monthly: p(500), next_due_on: "2026-04-10" }),
      cost(2, { merchant: "a", monthly: p(1500), next_due_on: null }),
      cost(3, { merchant: "c", monthly: p(900), next_due_on: "2026-04-02" }),
    ];
    const order = (sort: Filters["sort"]) => view(e, c, { sort }).bands[0]!.rows.map((r) => r.merchant_id);
    expect(order("monthly")).toEqual([2, 3, 1]);
    expect(order("name")).toEqual([2, 1, 3]);
    expect(order("due")).toEqual([3, 1, 2]);
  });

  it("passes flags, trial and the interval source through when the API sends them", () => {
    const r = view(
      [entry(1)],
      [
        cost(1, {
          flags: [{ code: "price_rise", on: "2026-03-01", before: p(999), after: p(1299) }],
          trial: { started_on: "2026-02-01", converts_on: "2026-03-03", converted: false },
          interval_source: "catalogue",
        }),
      ],
    ).bands[0]!.rows[0]!;
    expect([r.flags[0]!.code, r.trial?.converts_on, r.interval_source]).toEqual([
      "price_rise",
      "2026-03-03",
      "catalogue",
    ]);
  });

  it("gives no flags when the API does not send them yet", () => {
    const r = view([entry(1)], [cost(1)]).bands[0]!.rows[0]!;
    expect([r.flags, r.trial, r.interval_source]).toEqual([[], null, null]);
  });
});

describe("costText", () => {
  const row = (over: Partial<CostItem>) => view([entry(1)], [cost(1, over)]).bands[0]!.rows[0]!;
  it("says when a cost is estimated and why there is none", () => {
    expect(costText(row({ interval_source: "estimated" }))).toBe("Estimated from your charges");
    expect(costText(row({ interval_source: "catalogue" }))).toBe("Estimated: usual for this service");
    expect(costText(row({ interval_source: "detected" }))).toBeNull();
    const none = { monthly: null, interval: null, counted: false };
    expect(costText(row({ ...none, cost_note: "interval_needed", charge_count: 1 }))).toBe("Only one charge so far");
    expect(costText(row({ ...none, cost_note: "interval_needed", charge_count: 3 }))).toBe(
      "Charges too irregular to tell",
    );
    expect(costText(row({ ...none, cost_note: "rate_needed" }))).toBe("Needs an exchange rate");
    expect(costText(row({ ...none, cost_note: "no_charge" }))).toBe("No charge seen yet");
  });
});

describe("flagCount", () => {
  it("uses the API's count, else counts the items' flags, else is unknown", () => {
    expect(flagCount(costs([], { flag_count: 4 }))).toBe(4);
    const f = { code: "overlap" as const, on: null, before: null, after: null };
    expect(flagCount(costs([cost(1, { flags: [f, f] }), cost(2, { flags: [f] })]))).toBe(3);
    expect(flagCount(costs([cost(1)]))).toBeNull();
  });
});

describe("reasonText", () => {
  it("says why each entry is on the list", () => {
    expect(reasonText(entry(1, { reason: "known_service" }))).toBe("Known service");
    expect(reasonText(entry(1, { reason: "user_confirmed" }))).toBe("Confirmed by you on 1 Mar 2026");
    expect(reasonText(entry(1, { reason: "user_added" }))).toBe("Added by you on 1 Mar 2026");
    expect(reasonText(entry(1, { reason: "pattern" }))).toBe("Detected: 3 monthly charges");
    const bare = { on: "2026-03-01", charge_count: null, interval: null };
    expect(reasonText(entry(1, { reason: "pattern", evidence: bare }))).toBe("Detected from a repeating pattern");
  });
});
