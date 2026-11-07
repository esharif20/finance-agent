import { describe, expect, it } from "vitest";
import { poundStrip, splitSpent } from "./strip";

describe("poundStrip", () => {
  it("splits what came in into what went out and what stayed", () => {
    expect(poundStrip(341250, 247560)).toEqual({
      spentPence: 73,
      keptPence: 27,
      overspent: false,
    });
  });

  it("is full of spending, with no kept part, when more went out than came in", () => {
    expect(poundStrip(100000, 120000)).toEqual({
      spentPence: 100,
      keptPence: 0,
      overspent: true,
    });
  });

  it("has no strip when nothing came in", () => {
    expect(poundStrip(0, 5000)).toBeNull();
  });

  it("shares the spent pence between Categories and always adds up", () => {
    for (const weights of [[5, 3, 2], [1, 1, 1], [90000, 40000, 30000, 1], [7]]) {
      for (const spent of [0, 1, 27, 73, 100]) {
        const parts = splitSpent(spent, weights);
        expect(parts).toHaveLength(weights.length);
        expect(parts.reduce((a, b) => a + b, 0)).toBe(spent);
      }
    }
  });

  it("gives leftover pence to the largest remainders", () => {
    expect(splitSpent(10, [1, 1, 1])).toEqual([4, 3, 3]);
    expect(splitSpent(73, [5, 3, 2])).toEqual([36, 22, 15]);
  });

  it("has no parts without weight", () => {
    expect(splitSpent(50, [])).toEqual([]);
    expect(splitSpent(50, [0, 0])).toEqual([0, 0]);
  });

  it("always adds up to 100p", () => {
    for (const out of [0, 1, 333, 999, 5000, 99999]) {
      const s = poundStrip(100000, out);
      expect(s && s.spentPence + s.keptPence).toBe(100);
    }
  });
});
