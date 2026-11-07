import { describe, expect, it } from "vitest";
import { formatMoney, formatMoneyParts } from "./money";

const gbp = (minor: number) => ({ minor, currency: "GBP" });

describe("formatMoney", () => {
  it("formats minor units as pounds with thousands separators", () => {
    expect(formatMoney(gbp(341250))).toBe("£3,412.50");
    expect(formatMoney(gbp(5))).toBe("£0.05");
    expect(formatMoney(gbp(0))).toBe("£0.00");
  });

  it("uses a true minus, not a hyphen", () => {
    expect(formatMoney(gbp(-123456))).toBe("−£1,234.56");
  });

  it("can force a plus on positive amounts", () => {
    expect(formatMoney(gbp(93690), { sign: true })).toBe("+£936.90");
    expect(formatMoney(gbp(-93690), { sign: true })).toBe("−£936.90");
    expect(formatMoney(gbp(0), { sign: true })).toBe("£0.00");
  });

  it("stays exact beyond float precision", () => {
    expect(formatMoney(gbp(9007199254740991))).toBe("£90,071,992,547,409.91");
  });

  it("reads minor as hundredths in every currency, as the API stores them (#156)", () => {
    // Intl may show fewer digits (JPY, LAK), but never 100x the amount
    expect(formatMoney({ minor: 150000, currency: "JPY" })).toBe("JP¥1,500");
    expect(formatMoney({ minor: 150000, currency: "LAK" })).toMatch(/^[^0-9]*1,500$/);
    expect(formatMoney({ minor: 1234, currency: "EUR" })).toBe("€12.34");
  });
});

describe("formatMoneyParts", () => {
  it("separates the pence so they can be set smaller", () => {
    expect(formatMoneyParts(gbp(-123456))).toEqual({
      main: "−£1,234",
      fraction: ".56",
    });
    expect(formatMoneyParts({ minor: 150000, currency: "JPY" })).toEqual({ main: "JP¥1,500", fraction: "" });
  });
});
