import { describe, expect, it } from "vitest";
import { addsDetail, joinParts, monthKey, monthLabel } from "./txnText";

describe("addsDetail", () => {
  it.each([
    ["Spotify", "Spotify P487971923", false],
    ["Spotify", "Spotify Limited", false],
    ["Spotify", "SPOTIFY LTD 12345 GB", false],
    ["Spotify", "Spotify", false],
    ["Acme Cafe", "Acme Cafe", false],
    ["Acme Cafe", "Card payment to Acme Cafe", false],
    ["Acme Cafe", "Acme Cafe Oxford Street branch", true],
    ["Acme Cafe", "Refund for lunch order", true],
    ["Acme", "Gift for Sam", true],
  ])("%s / %s -> %s", (merchant, description, expected) => {
    expect(addsDetail(merchant, description)).toBe(expected);
  });
  it("treats an unknown merchant as nothing to add", () => {
    expect(addsDetail(null, "anything")).toBe(false);
  });
});

describe("joinParts", () => {
  it("joins only non-empty parts", () => {
    expect(joinParts(["a", "b"])).toBe("a · b");
    expect(joinParts(["a", undefined])).toBe("a");
    expect(joinParts(["", "b", null, "  "])).toBe("b");
    expect(joinParts([undefined, ""])).toBe("");
  });
});

describe("month grouping", () => {
  it("keys and labels by UTC month", () => {
    expect(monthKey("2026-03-31")).toBe("2026-03");
    expect(monthLabel("2026-03")).toBe("March 2026");
  });
});
