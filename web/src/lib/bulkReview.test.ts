import { describe, expect, it } from "vitest";
import type { ReviewItem } from "@/api/client";
import { GROUP_CAP, groupBySuggestion, isBig, pages, payments, score, selected, shown } from "./bulkReview";

const mk = (id: string, spend: number, sugg: [string, string][], over: Partial<ReviewItem> = {}): ReviewItem => ({
  id: `category:${id}`,
  type: "category",
  version: `v${id}`,
  weight: 2,
  title: `M${id}`,
  display_name: over.title ?? `M${id}`,
  merchant_id: Number(id),
  transaction_ids: [1],
  choices: [],
  suggestions: sugg,
  skipped: false,
  spend_minor: spend,
  currency: "GBP",
  ...over,
});
const FOOD: [string, string] = ["Food & Drink", "Groceries"];
const CAR: [string, string] = ["Transport", "Fuel"];

describe("groupBySuggestion", () => {
  it("groups by the top suggestion and drops what can't be bulk-accepted", () => {
    const g = groupBySuggestion([
      mk("1", 100, [FOOD]),
      mk("2", 150, [CAR, FOOD]),
      mk("3", 100, []),
      mk("4", 100, [FOOD], { skipped: true }),
      mk("5", 100, [FOOD], { type: "possible_duplicate" }),
      mk("6", 100, [FOOD]),
    ]);
    expect(g.map((x) => [x.key, x.items.map((i) => i.id)])).toEqual([
      ["Transport/Fuel", ["category:2"]],
      ["Food & Drink/Groceries", ["category:1", "category:6"]],
    ]);
  });

  it("orders items and groups by spend x uncertainty, biggest first", () => {
    const small = mk("1", 500, [FOOD]);
    const torn = mk("2", 300, [FOOD, CAR, ["Shopping", "Gifts"]]); // 900
    const big = mk("3", 5000, [CAR]);
    expect(score(torn)).toBe(900);
    const g = groupBySuggestion([small, torn, big]);
    expect(g.map((x) => x.key)).toEqual(["Transport/Fuel", "Food & Drink/Groceries"]);
    expect(g[1]?.items.map((i) => i.id)).toEqual(["category:2", "category:1"]);
  });

  it("breaks ties by name so the order is stable", () => {
    const g = groupBySuggestion([mk("1", 100, [FOOD], { title: "b" }), mk("2", 100, [FOOD], { title: "a" })]);
    expect(g[0]?.items.map((i) => i.title)).toEqual(["a", "b"]);
  });
});

describe("default ticks and the cap", () => {
  it("leaves unticked every Merchant scoring below the group's median", () => {
    const [g] = groupBySuggestion([mk("1", 100, [FOOD]), mk("2", 200, [FOOD]), mk("3", 300, [FOOD]), mk("4", 400, [FOOD])]);
    expect([...g!.off].sort()).toEqual(["category:1", "category:2"]); // median 250
    expect(selected(g!, new Set()).map((i) => i.id)).toEqual(["category:4", "category:3"]);
  });

  it("unticks by spend, not by how torn the model is", () => {
    const torn = mk("1", 100, [FOOD, CAR, ["Shopping", "Gifts"]]); // score 300, spend lowest
    const [g] = groupBySuggestion([torn, mk("2", 200, [FOOD]), mk("3", 250, [FOOD])]);
    expect([...g!.off]).toEqual(["category:1"]); // median spend 200
  });

  it("ticks the whole group when the scores are level, and a lone Merchant", () => {
    const [g] = groupBySuggestion([mk("1", 100, [FOOD]), mk("2", 100, [FOOD])]);
    expect(g!.off.size).toBe(0);
    expect(groupBySuggestion([mk("1", 5, [CAR])])[0]?.off.size).toBe(0);
  });

  it("a click flips the default either way", () => {
    const [g] = groupBySuggestion([mk("1", 100, [FOOD]), mk("2", 300, [FOOD])]);
    expect(selected(g!, new Set(["category:1", "category:2"])).map((i) => i.id)).toEqual(["category:1"]);
  });

  it("offers a big group GROUP_CAP rows at a time, a small one whole", () => {
    const many = Array.from({ length: 40 }, (_, n) => mk(String(n + 1), 1000 - n, [FOOD]));
    const [g] = groupBySuggestion(many);
    expect(isBig(g!)).toBe(true);
    expect(pages(g!)).toBe(3);
    expect(shown(g!, 0)).toHaveLength(GROUP_CAP);
    expect(shown(g!, 2)).toHaveLength(10);
    expect(shown(g!, 9)).toHaveLength(10); // a page past the end clamps
    const [small] = groupBySuggestion(many.slice(0, GROUP_CAP));
    expect(isBig(small!)).toBe(false);
    expect(shown(small!, 0)).toHaveLength(GROUP_CAP);
  });

  it("counts payments from weight, never money", () => {
    expect(payments([mk("1", 1, [FOOD], { weight: 3 }), mk("2", 1, [FOOD], { weight: 4 })])).toBe(7);
  });
});

describe("selected", () => {
  it("leaves out unticked items", () => {
    const [g] = groupBySuggestion([mk("1", 1, [FOOD]), mk("2", 1, [FOOD])]);
    expect(selected(g!, new Set(["category:1"])).map((i) => i.id)).toEqual(["category:2"]);
  });
});
