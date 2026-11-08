// @vitest-environment jsdom
import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { formatMoney } from "@/lib/money";
import Landing from "./Landing";
import { Preloader, TALLY } from "./Preloader";
import { OVERVIEW } from "./story/story.fixture";

const mediaStub = (reduce: boolean) => (q: string) => ({
  matches: reduce && q.includes("prefers-reduced-motion"),
  media: q,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
});

class NoObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("IntersectionObserver", NoObserver);
  sessionStorage.clear();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** What each wheel lands on: the digit at index `--to - 10` of its strip. */
const settled = (root: ParentNode) =>
  [...root.querySelectorAll<HTMLElement>(".pl-above .pl-count > span")]
    .map((s) => {
      const strip = s.querySelector<HTMLElement>(".pl-wheel-in");
      if (!strip) return s.textContent;
      const to = Number(strip.style.getPropertyValue("--to"));
      return strip.children[to]?.textContent;
    })
    .join("");

describe("preloader, the tally", () => {
  it("starts at zero and settles on the made-up ledger's own figure", () => {
    expect(TALLY).toBe(formatMoney(OVERVIEW.spent));
    vi.stubGlobal("matchMedia", mediaStub(false));
    const { container } = render(<Preloader still={false} onGo={() => {}} onDone={() => {}} />);
    expect(settled(container)).toBe(TALLY);
    // every strip counts 0–9 in order from zero: an odometer rolling up, with nothing else on it
    for (const strip of container.querySelectorAll(".pl-wheel-in"))
      [...strip.children].forEach((c, i) => expect(c.textContent).toBe(String(i % 10)));
  });

  it("holds the hero back while loading and hands over within the 2.5 s cap", () => {
    vi.stubGlobal("matchMedia", mediaStub(false));
    const onGo = vi.fn();
    const onDone = vi.fn();
    render(<Preloader still={false} onGo={onGo} onDone={onDone} />);
    act(() => vi.advanceTimersByTime(1000));
    expect(onGo).not.toHaveBeenCalled(); // the minimum display time
    act(() => vi.advanceTimersByTime(1600));
    expect(onGo).toHaveBeenCalledTimes(1);
    act(() => vi.advanceTimersByTime(800));
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("under reduced motion is only a short fade over the settled frame", () => {
    vi.stubGlobal("matchMedia", mediaStub(true));
    const onDone = vi.fn();
    const { container } = render(<Preloader still onGo={() => {}} onDone={onDone} />);
    expect(container.querySelectorAll(".pl-wheel-in:not([data-on])")).toHaveLength(0);
    act(() => vi.advanceTimersByTime(400));
    expect(onDone).toHaveBeenCalled();
  });

  it("never plays again for a returning visitor", () => {
    vi.stubGlobal("matchMedia", mediaStub(false));
    const first = render(<Landing />);
    expect(first.container.querySelector(".pl")).not.toBeNull();
    act(() => vi.advanceTimersByTime(3500));
    expect(first.container.querySelector(".pl")).toBeNull();
    first.unmount();
    const again = render(<Landing />);
    expect(again.container.querySelector(".pl")).toBeNull();
  });
});
