import { describe, expect, it } from "vitest";
import { confidenceWord, typeTitle } from "./insights";

// synthetic
describe("insight shaping", () => {
  it("names each type in plain words, and falls back for an unknown one", () => {
    expect(typeTitle("price_increase")).toBe("Price rise");
    expect(typeTitle("shortfall")).toBe("Cash squeeze");
    expect(typeTitle("brand_new_kind")).toBe("Brand new kind");
  });
  it("turns a confidence percent into a word", () => {
    expect(confidenceWord(90)).toBe("High confidence");
    expect(confidenceWord(70)).toBe("Fair confidence");
    expect(confidenceWord(50)).toBe("Low confidence");
  });
});
