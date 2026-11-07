import { describe, expect, it } from "vitest";
import { buildView, flagCount, reasonText, type CostItem, type Entry } from "./subscriptions";

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
  groups: [
    { kind: "subscription", group: "Streaming", monthly: p(2000) },
    { kind: "subscription", group: "Software", monthly: p(500) },
    { kind: "bill", group: "Energy", monthly: p(9000) },
  ],
  rate_needed: 0,
  ...over,
});

describe("buildView", () => {
  it("splits Subscriptions from Bills and orders groups as the taxonomy does", () => {
    const e = [entry(1, { group: "Software" }), entry(2), entry(3, { kind: "bill", group: "Energy" })];
    const c = costs([
      cost(1, { group: "Software" }),
      cost(2),
      cost(3, { kind: "bill", group: "Energy", monthly: p(9000) }),
    ]);
    const v = buildView(e, c);
    expect(v.sections.map((s) => s.kind)).toEqual(["subscription", "bill"]);
    expect(v.sections[0]!.groups.map((g) => g.name)).toEqual(["Streaming", "Software"]);
    expect(v.sections[0]!.total).toEqual(p(2500));
    expect(v.sections[0]!.groups[0]!.total).toEqual(p(2000));
    expect(v.sections[1]!.groups[0]!.rows[0]!.merchant).toBe("Svc 3");
  });

  it("keeps suggestions and removed entries out of the groups", () => {
    const e = [
      entry(1, { status: "suggested", reason: "pattern" }),
      entry(2, { status: "not_subscription", kind: null, group: null }),
      entry(3),
    ];
    const c = costs([cost(1, { status: "suggested", counted: false }), cost(3)]);
    const v = buildView(e, c);
    expect(v.suggested.map((s) => s.merchant_id)).toEqual([1]);
    const ids = v.sections.flatMap((s) => s.groups.flatMap((g) => g.rows.map((r) => r.merchant_id)));
    expect(ids).toEqual([3]);
  });

  it("lists counted rows before cancelled ones", () => {
    const e = [entry(1, { status: "user_cancelled" }), entry(2)];
    const c = costs([cost(1, { status: "cancelled", counted: false }), cost(2)]);
    const rows = buildView(e, c).sections[0]!.groups[0]!.rows;
    expect(rows.map((r) => r.merchant_id)).toEqual([2, 1]);
    expect(rows[1]!.counted).toBe(false);
  });

  it("drops empty sections", () => {
    const v = buildView([entry(1)], costs([cost(1)]));
    expect(v.sections.map((s) => s.kind)).toEqual(["subscription"]);
  });

  it("passes flags and trial through when the API sends them", () => {
    const c = costs([
      cost(1, {
        flags: [{ code: "price_rise", on: "2026-03-01", before: p(999), after: p(1299) }],
        trial: { started_on: "2026-02-01", converts_on: "2026-03-03", converted: false },
      }),
    ]);
    const r = buildView([entry(1)], c).sections[0]!.groups[0]!.rows[0]!;
    expect(r!.flags[0]!.code).toBe("price_rise");
    expect(r!.trial?.converts_on).toBe("2026-03-03");
  });

  it("gives no flags when the API does not send them yet", () => {
    const r = buildView([entry(1)], costs([cost(1)])).sections[0]!.groups[0]!.rows[0]!;
    expect(r.flags).toEqual([]);
    expect(r.trial).toBeNull();
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
