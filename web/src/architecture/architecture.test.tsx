import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Architecture from "./Architecture";
import { FLOW, SAMPLE } from "./nodes";

const media = (reduce: boolean, wide: boolean) => (q: string) => ({
  matches: (reduce && q.includes("prefers-reduced-motion")) || (wide && q.includes("min-width")),
  addEventListener: () => {},
  removeEventListener: () => {},
});

class NoIO {
  observe() {}
  unobserve() {}
  disconnect() {}
}

function setup(reduce: boolean, wide: boolean) {
  vi.stubGlobal("matchMedia", media(reduce, wide));
  vi.stubGlobal("IntersectionObserver", NoIO);
  Element.prototype.scrollIntoView = () => {};
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("architecture page", () => {
  it("opens a node's detail under it on a phone, and closes it again", async () => {
    setup(false, false);
    render(<Architecture />);
    const node = screen.getByRole("button", { name: /Grounding check/ });
    expect(node).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(node);
    expect(node).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("region", { name: "Grounding check" })).toHaveTextContent("ADR 0007");
    await userEvent.click(node);
    expect(screen.queryByRole("region", { name: "Grounding check" })).toBeNull();
  });

  it("works from the keyboard", async () => {
    setup(false, false);
    render(<Architecture />);
    const node = screen.getByRole("button", { name: /Contextual Thompson bandit/ });
    node.focus();
    await userEvent.keyboard("{Enter}");
    expect(screen.getByRole("region", { name: "Contextual Thompson bandit" })).toHaveTextContent("In shadow");
  });

  it("plays the sample question step by step", async () => {
    setup(false, true);
    vi.useFakeTimers();
    render(<Architecture />);
    act(() => screen.getByRole("button", { name: "Play" }).click());
    expect(screen.getByText(SAMPLE.steps[0])).toBeInTheDocument();
    for (let i = 1; i < FLOW.length; i++) act(() => vi.advanceTimersByTime(1500));
    expect(screen.getByText(SAMPLE.steps[4])).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Reply and evidence" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Play again" })).toBeInTheDocument();
  });

  it("rests on the whole path, lit, with reduced motion", () => {
    setup(true, true);
    render(<Architecture />);
    for (const n of FLOW) expect(screen.getByRole("button", { name: new RegExp(n.title) })).toHaveAttribute("data-lit");
    expect(screen.getByRole("button", { name: "Play again" })).toBeInTheDocument();
  });
});
