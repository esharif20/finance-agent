import { describe, expect, it } from "vitest";
import { poundStrip } from "./strip";

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

  it("always adds up to 100p", () => {
    for (const out of [0, 1, 333, 999, 5000, 99999]) {
      const s = poundStrip(100000, out);
      expect(s && s.spentPence + s.keptPence).toBe(100);
    }
  });
});
