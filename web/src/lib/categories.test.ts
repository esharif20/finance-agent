import { describe, expect, it } from "vitest";
import { OTHER_COLOUR, REVIEW_COLOUR, groupSpending } from "./categories";

const c = (name: string, minor: number) => ({
  name,
  amount: { minor, currency: "GBP" },
});

const eight = [
  c("Housing", 90000),
  c("Food & Drink", 40000),
  c("Shopping", 30000),
  c("Transport", 20000),
  c("Travel", 15000),
  c("Health", 9000),
  c("Entertainment", 5000),
  c("Uncategorised", 1000),
];

describe("groupSpending", () => {
  it("colours the top 5 Categories with their own hue and greys the rest", () => {
    const { rows } = groupSpending(eight);

    expect(rows.slice(0, 5).map((r) => r.colour)).toEqual([
      "var(--cat-housing)",
      "var(--cat-food-drink)",
      "var(--cat-shopping)",
      "var(--cat-transport)",
      "var(--cat-travel)",
    ]);
    expect(rows.slice(5).map((r) => r.colour)).toEqual([OTHER_COLOUR, OTHER_COLOUR, OTHER_COLOUR]);
  });

  it("draws the rest as one grey slice whose amount is their sum", () => {
    const { slices } = groupSpending(eight);

    expect(slices).toHaveLength(6);
    expect(slices[5]).toMatchObject({ name: "Other categories", minor: 15000, colour: OTHER_COLOUR });
  });

  it("lists every Category in the legend rows, none dropped", () => {
    expect(groupSpending(eight).rows.map((r) => r.name)).toEqual(eight.map((x) => x.name));
  });

  it("gives shares in whole percent of the drawn total", () => {
    const { rows } = groupSpending([c("Housing", 75000), c("Travel", 25000)]);
    expect(rows.map((r) => r.percent)).toEqual([75, 25]);
  });

  it("never colours Uncategorised, even when it is large", () => {
    const { rows } = groupSpending([c("Uncategorised", 99000), c("Housing", 1000)]);
    expect(rows.find((r) => r.name === "Uncategorised")?.colour).toBe(OTHER_COLOUR);
    expect(rows.find((r) => r.name === "Housing")?.colour).toBe("var(--cat-housing)");
  });

  it("keeps a net-negative Category (Refunds exceed spending) out of the drawing", () => {
    const { slices, rows } = groupSpending([c("Housing", 50000), c("Shopping", -2000)]);
    expect(slices.map((s) => s.name)).toEqual(["Housing"]);
    expect(rows.find((r) => r.name === "Shopping")).toMatchObject({ colour: OTHER_COLOUR, percent: 0 });
  });

  it("has nothing to draw when there is no spending", () => {
    expect(groupSpending([]).slices).toEqual([]);
  });

  it("keeps To review out of the top 5: its own amber slice, never merged into the grey", () => {
    const withReview = [c("Housing", 90000), c("To review", 50000), c("Food & Drink", 40000), c("Shopping", 30000)];
    const { slices, rows } = groupSpending(withReview);

    expect(slices.map((s) => s.name)).toEqual(["Housing", "To review", "Food & Drink", "Shopping"]);
    expect(slices[1]).toMatchObject({ minor: 50000, colour: REVIEW_COLOUR });
    expect(rows.find((r) => r.name === "To review")?.colour).toBe(REVIEW_COLOUR);
    // it takes none of the five coloured places
    expect(groupSpending([...eight, c("To review", 50000)]).rows.slice(0, 6).filter((r) => r.colour !== OTHER_COLOUR).map((r) => r.name)).toContain("Travel");
  });
});
