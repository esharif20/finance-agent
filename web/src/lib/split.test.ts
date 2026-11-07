import { describe, expect, it } from "vitest";
import { parsePence, splitState } from "./split";

describe("parsePence", () => {
  it.each([
    ["12.50", 1250],
    ["12.5", 1250],
    ["12", 1200],
    ["0.07", 7],
    [".5", 50],
    ["£3.20", 320],
    [" 1,204.10 ", 120410],
  ])("reads %s as %i pence", (text, pence) => expect(parsePence(text)).toBe(pence));

  it.each(["", "abc", "1.234", "-5", "1e3", "1.2.3", "0", "0.00", "."])("refuses %j", (text) =>
    expect(parsePence(text)).toBeNull(),
  );
});

describe("splitState", () => {
  const row = (amount: string) => ({ amount });

  it("fills the last part with the remainder in whole pence", () => {
    const s = splitState(-1250, [row("5.10"), row("")]);
    expect(s).toMatchObject({ remainder: -740, valid: true });
    expect(s.minors).toEqual([-510, -740]);
  });

  it("keeps the sign of the payment, so a refund splits as money in", () => {
    expect(splitState(1000, [row("2.50"), row("")]).minors).toEqual([250, 750]);
  });

  it("cannot be saved while the typed parts reach or pass the total", () => {
    const over = splitState(-1000, [row("6"), row("5"), row("")]);
    expect(over.valid).toBe(false);
    expect(over.remainder).toBe(100); // negative of the payment's sign: over by £1
    expect(splitState(-1000, [row("10"), row("")]).valid).toBe(false); // last part would be zero
  });

  it("cannot be saved with a part left blank or unreadable", () => {
    expect(splitState(-1000, [row(""), row("")]).valid).toBe(false);
    expect(splitState(-1000, [row("1.234"), row("")]).valid).toBe(false);
  });

  it("needs two to ten parts", () => {
    expect(splitState(-1000, [row("")]).valid).toBe(false);
    const eleven = Array.from({ length: 11 }, () => row("0.10"));
    expect(splitState(-1000, eleven).valid).toBe(false);
    const ten = Array.from({ length: 10 }, () => row("0.10"));
    expect(splitState(-1000, ten).valid).toBe(true);
  });

  it("is exact for any total: valid parts always sum to the payment", () => {
    for (let total = 2; total < 400; total += 7) {
      const first = String(Math.max(1, Math.floor(total / 3)) / 100);
      const s = splitState(-total, [row(first), row("")]);
      expect(s.valid).toBe(true);
      expect(s.minors.reduce((a, b) => a + b, 0)).toBe(-total);
    }
  });
});
