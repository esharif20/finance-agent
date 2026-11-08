import { describe, expect, it } from "vitest";
import { parseAmount, riskCopy } from "./whatif";

// synthetic
describe("what-if input", () => {
  it("parses pounds to integer pence without floats", () => {
    expect(parseAmount("12.50")).toBe(1250);
    expect(parseAmount("£1,200")).toBe(120000);
    expect(parseAmount("0.1")).toBe(10);
    expect(parseAmount("19.99")).toBe(1999);
  });
  it("refuses anything else", () => {
    for (const bad of ["", "0", "0.00", "-5", "1.234", "abc", "1e3", "99999999999999"])
      expect(parseAmount(bad)).toBeNull();
  });
  it("describes each risk", () => {
    expect(riskCopy("none")).toMatch(/stays above/i);
    expect(riskCopy("below_buffer")).toMatch(/buffer/i);
    expect(riskCopy("below_zero")).toMatch(/below zero/i);
  });
});
