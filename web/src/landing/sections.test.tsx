// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import path from "node:path";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HowItWorks } from "./HowItWorks";
import { betaPdf, Learns, strengths } from "./Learns";
import { Opener } from "./Opener";
import { SCRIPT, Terminal } from "./terminal/Terminal";

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
const setup = (reduce: boolean) => {
  vi.stubGlobal("matchMedia", mediaStub(reduce));
  vi.stubGlobal("IntersectionObserver", NoObserver);
};
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("how it works", () => {
  it("names the only three destinations and the no-payments rule", () => {
    setup(false);
    render(<HowItWorks />);
    expect(screen.getByRole("heading", { name: /how a question gets its answer/i })).toBeTruthy();
    expect(screen.getByText("Only if you opt in")).toBeTruthy();
    expect(screen.getByText(/never moves money/i)).toBeTruthy();
  });
});

describe("it learns you", () => {
  it("a Beta density integrates to about 1", () => {
    let s = 0;
    for (let i = 1; i < 1000; i++) s += betaPdf(i / 1000, 4, 2) / 1000;
    expect(s).toBeCloseTo(1, 2);
  });

  it("Bradley-Terry ranks the item kept every time above the one cut", () => {
    const s = strengths([[0, 1, 3]], 2);
    expect(s[0]).toBeGreaterThan(s[1] ?? 0);
    expect((s[0] ?? 0) + (s[1] ?? 0)).toBeCloseTo(0, 5); // the prior keeps them centred
  });

  it("dismissing an Insight moves it down", () => {
    setup(false);
    const { container } = render(<Learns />);
    const first = () => container.querySelector(".lp-learn-cards li .lp-learn-title")?.textContent;
    const top = first();
    const btn = screen.getByRole("button", { name: `Dismiss: ${top}` });
    for (let i = 0; i < 4; i++) fireEvent.click(btn);
    expect(first()).not.toBe(top);
  });
});

describe("terminal", () => {
  it("shows the final frame under reduced motion", () => {
    setup(true);
    vi.stubGlobal("HTMLCanvasElement", class extends HTMLElement {});
    const { container } = render(<Terminal />);
    expect(container.textContent).toContain("opening the demo…");
    expect(container.textContent).toContain("fa subs");
    expect(screen.queryByRole("button", { name: "Replay" })).toBeNull();
    expect(SCRIPT.at(-1)?.kind).toBe("out");
  });

  it("its fixture is synthetic", () => {
    const f = readFileSync(path.join(import.meta.dirname, "terminal/terminal.fixture.ts"), "utf8");
    expect(f.startsWith("// synthetic")).toBe(true);
    const a = readFileSync(path.join(import.meta.dirname, "accounts.fixture.ts"), "utf8");
    expect(a.startsWith("// synthetic")).toBe(true);
  });
});

describe("demo opener", () => {
  it("waits at least 0.8 s, then opens /app once the API answers", async () => {
    setup(false);
    vi.useFakeTimers();
    vi.stubGlobal("fetch", () => Promise.resolve(new Response("[]")));
    const go = vi.fn();
    render(<Opener still={false} onGone={() => {}} go={go} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(700);
    });
    expect(go).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(go).toHaveBeenCalledWith("/app");
  });

  it("holds until the data is ready", async () => {
    setup(false);
    vi.useFakeTimers();
    let answer = () => {};
    vi.stubGlobal("fetch", () => new Promise<Response>((r) => (answer = () => r(new Response("[]")))));
    const go = vi.fn();
    render(<Opener still={false} onGone={() => {}} go={go} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(go).not.toHaveBeenCalled();
    await act(async () => {
      answer();
      await vi.advanceTimersByTimeAsync(400);
    });
    expect(go).toHaveBeenCalledWith("/app");
  });
});
