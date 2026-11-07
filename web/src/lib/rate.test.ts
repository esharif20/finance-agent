import { describe, expect, it } from "vitest";
import { checkRate } from "./rate";

// synthetic
describe("checkRate", () => {
  it.each(["0.85", "1", "1234567.123456789012", "0.000000000001"])("accepts %s", (v) => {
    expect(checkRate(v)).toBeNull();
  });

  it.each([
    ["", /enter/i],
    ["0", /more than zero/],
    ["0.00", /more than zero/],
    ["-0.8", /digits/],
    ["+0.8", /digits/],
    ["1e-3", /digits/],
    ["0,85", /digits/],
    ["NaN", /digits/],
    ["Infinity", /digits/],
    ["0.8.5", /digits/],
    ["٠.٨٥", /digits/], // Arabic-Indic digits
    ["０.８５", /digits/], // full-width digits
    ["12345678", /digits/], // more than 7 whole digits
    ["0.1234567890123", /12 decimal/],
  ])("refuses %j", (v, msg) => {
    expect(checkRate(v)).toMatch(msg);
  });

  it("trims surrounding spaces", () => {
    expect(checkRate(" 0.85 ")).toBeNull();
  });
});
