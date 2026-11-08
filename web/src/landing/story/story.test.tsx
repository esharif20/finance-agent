// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import path from "node:path";
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Story } from "./Story";
import { MAX_LAYERS, PILE } from "./Pile";
import { MERCHANTS, PRINCIPLES } from "./story.fixture";

const mediaStub = (reduce: boolean) => (q: string) => ({
  matches: reduce && q.includes("prefers-reduced-motion"),
  media: q,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
});

afterEach(() => vi.unstubAllGlobals());

/** jsdom has no IntersectionObserver; the reveals only need one that never fires. */
class NoObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}
const motionOn = () => {
  vi.stubGlobal("matchMedia", mediaStub(false));
  vi.stubGlobal("IntersectionObserver", NoObserver);
};

const transformed = (root: HTMLElement) =>
  [root, ...root.querySelectorAll<HTMLElement>("*")].filter((el) => {
    const t = el.style.transform;
    return (t && t !== "none") || el.style.opacity !== "" || el.style.fontWeight !== "";
  });

describe("story under prefers-reduced-motion", () => {
  it("renders every chapter in its final state with no transforms", () => {
    vi.stubGlobal("matchMedia", mediaStub(true));
    const { container } = render(<Story />);
    expect(transformed(container)).toEqual([]);
    // pile: all three cards, flat
    expect(container.querySelectorAll("[data-layer]").length).toBe(PILE.length);
    // gateway: both the real line and what the cloud sees, side by side, no scrambled digits
    const gate = screen.getByRole("region", { name: /what leaves your mac/i });
    expect(within(gate).getAllByText("£42.10").length).toBeGreaterThan(0);
    expect(within(gate).getByText("{P1}")).toBeTruthy();
    expect(within(gate).getByText("{M1}")).toBeTruthy();
    // focus list: every principle readable
    for (const p of PRINCIPLES) expect(screen.getByText(p)).toBeTruthy();
    // the wave wipe rests fully drawn
    expect(container.querySelectorAll("[data-wave]").length).toBe(1);
  });
});

describe("story with motion", () => {
  it("animates the pile and the focus list", () => {
    motionOn();
    const { container } = render(<Story />);
    expect(transformed(container).length).toBeGreaterThan(0);
  });

  it("the pile never exceeds 3 layers", () => {
    motionOn();
    expect(MAX_LAYERS).toBe(3);
    expect(PILE.length).toBeLessThanOrEqual(MAX_LAYERS);
    const { container } = render(<Story />);
    expect(container.querySelectorAll("[data-layer]").length).toBeLessThanOrEqual(MAX_LAYERS);
  });

  it("has at most one wave wipe", () => {
    motionOn();
    const { container } = render(<Story />);
    expect(container.querySelectorAll("[data-wave]").length).toBeLessThanOrEqual(1);
  });
});

describe("story fixture", () => {
  const file = readFileSync(path.join(import.meta.dirname, "story.fixture.ts"), "utf8");

  it("starts with the synthetic marker", () => {
    expect(file.startsWith("// synthetic")).toBe(true);
  });

  it("uses no real Merchant or bank names", () => {
    const real =
      /\b(tesco|sainsbury|asda|aldi|lidl|waitrose|morrisons|co-?op|pret|costa|greggs|starbucks|amazon|uber|deliveroo|tfl|netflix|spotify|revolut|monzo|starling|barclays|hsbc|lloyds|natwest|santander|nationwide|halifax|chase|amex|american express|visa|mastercard)\b/i;
    expect(file).not.toMatch(real);
    for (const m of MERCHANTS) expect(m).not.toMatch(real);
  });
});
