import { describe, expect, it } from "vitest";
import type { ReviewItem } from "@/api/client";
import { groupBySuggestion, score, selected } from "./bulkReview";

const mk = (id: string, spend: number, sugg: [string, string][], over: Partial<ReviewItem> = {}): ReviewItem => ({
  id: `category:${id}`,
  type: "category",
  version: `v${id}`,
  weight: 2,
  title: `M${id}`,
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

describe("selected", () => {
  it("leaves out unticked items", () => {
    const [g] = groupBySuggestion([mk("1", 1, [FOOD]), mk("2", 2, [FOOD])]);
    expect(selected(g!, new Set(["category:1"])).map((i) => i.id)).toEqual(["category:2"]);
  });
});
