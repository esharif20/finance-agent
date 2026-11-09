import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Architecture from "./Architecture";
import { FLOW, NODES, SAMPLE } from "./nodes";

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
    const node = screen.getByRole("button", { name: /Grounding check.+/ });
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
    const node = screen.getByRole("button", { name: /Contextual Thompson bandit.+/ });
    node.focus();
    await userEvent.keyboard("{Enter}");
    expect(screen.getByRole("region", { name: "Contextual Thompson bandit" })).toHaveTextContent("In shadow");
  });

  it("plays the sample question step by step on a phone", async () => {
    setup(false, false);
    vi.useFakeTimers();
    render(<Architecture />);
    act(() => screen.getByRole("button", { name: "Play" }).click());
    expect(screen.getByText(SAMPLE.steps[0])).toBeInTheDocument();
    for (let i = 1; i < FLOW.length; i++) act(() => vi.advanceTimersByTime(1500));
    expect(screen.getByText(SAMPLE.steps[4])).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Play again" })).toBeInTheDocument();
  });

  it("holds the path in a scroll scene on a wide screen with a fine pointer", () => {
    setup(false, true);
    const { container } = render(<Architecture />);
    expect(container.querySelector(".ar-track .ar-scene")).not.toBeNull();
    expect(screen.getByText(/Scroll, or press Play/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Planner" })).toBeInTheDocument();
  });

  it("lights a box's neighbours in a chart and shows its one-line note", async () => {
    setup(false, true);
    render(<Architecture />);
    const chart = screen.getByRole("group", { name: /^The system/ });
    const ledger = within(chart).getByRole("img", { name: /^Ledger\./ });
    await userEvent.hover(ledger);
    expect(within(chart).getByRole("img", { name: /^Engine\./ })).not.toHaveAttribute("data-dim");
    expect(within(chart).getByRole("img", { name: /^Gemini\./ })).toHaveAttribute("data-dim");
    expect(screen.getByText(/integer pence, in one SQLCipher database/)).toBeInTheDocument();
  });

  it("draws planned orchestration apart from the built graph", () => {
    setup(false, true);
    render(<Architecture />);
    const chart = screen.getByRole("group", { name: /Assistant's graph/ });
    expect(within(chart).getByText("Orchestration")).toBeInTheDocument();
    expect(within(chart).getByText("Planned")).toBeInTheDocument();
    expect(within(chart).getByRole("img", { name: /^Learned model router\. Planned/ })).toHaveAttribute("data-outside");
  });

  it("shows a picture beside a security decision", async () => {
    setup(false, false);
    render(<Architecture />);
    await userEvent.click(screen.getByRole("button", { name: /Gateway placeholders.+/ }));
    expect(screen.getByRole("region", { name: "Gateway placeholders" })).toHaveTextContent("{P1} at {M1} on {D1}");
  });

  it("shows the poster facts as their final numbers, and keeps the title one heading", () => {
    setup(true, true);
    render(<Architecture />);
    const facts = within(screen.getByRole("list", { name: "At a glance" })).getAllByRole("listitem");
    expect(facts.map((f) => f.querySelector(".ar-count")?.textContent)).toEqual([String(NODES.length), "2", "1", "0"]);
    expect(screen.getByRole("heading", { level: 1, name: "How the Assistant answers" })).toBeInTheDocument();
    expect(document.querySelector(".ar-way")).toHaveAttribute("href", "#ar-toc");
  });

  it("rests on the whole path, lit, with reduced motion", () => {
    setup(true, true);
    render(<Architecture />);
    for (const n of FLOW) expect(screen.getByRole("button", { name: new RegExp(`${n.title}.+`) })).toHaveAttribute("data-lit");
    expect(screen.getByRole("button", { name: "Play again" })).toBeInTheDocument();
  });
});
