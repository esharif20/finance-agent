import { screen, waitFor, within } from "@testing-library/react";
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "@/App";

// synthetic
const gbp = (minor: number) => ({ minor, currency: "GBP" });
let calls: { url: string; method: string; body?: string }[];
let answered: boolean | null;
let memories: { id: number; text: string; tags: string[] }[];

const briefing = () => ({
  week: "2026-03-02",
  insights: [
    {
      id: 1,
      type: "price_increase",
      key: "Streamly",
      category: "Subscriptions",
      raised_on: "2026-03-01",
      impact_minor: 2400,
      confidence: 70,
      evidence: [1],
      explanation: "Streamly went up; that is about £24.00 a year more.",
      signal: null,
      relevance: { liked: 0, disliked: 0 },
    },
  ],
  forecast: { month: "2026-03", point_minor: 123400, lower_minor: 100000, upper_minor: 150000, recurring_minor: 50000, left_out: 1 },
  next_payday: "2026-03-25",
  renewals: [{ insight_id: 2, merchant: "Gym Club", due_on: "2026-03-06", amount_minor: 3500 }],
  regret: [
    { transaction_id: 7, posted_on: "2026-03-01", description: "SYNTH CAFE", merchant: "Synth Cafe", amount: gbp(-1250), worth_it: answered },
  ],
  insufficient: [],
});

beforeEach(() => {
  calls = [];
  answered = null;
  memories = [{ id: 3, text: "Saving £200 a month for a bike", tags: ["goal"] }];
  window.location.hash = "#briefing";
  localStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body as string | undefined });
      if (method === "POST" && url.startsWith("/api/briefing/regret/")) answered = JSON.parse(init?.body as string).worth_it;
      if (url.startsWith("/api/briefing")) return method === "GET" ? Response.json(briefing()) : Response.json({});
      if (url.startsWith("/api/memories")) return Response.json({ items: memories });
      if (method === "DELETE") return Response.json({ deleted: 1 });
      if (url.startsWith("/api/summary") || url.startsWith("/api/spending")) return new Response("{}", { status: 404 });
      return Response.json([]);
    }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = "";
  document.documentElement.removeAttribute("data-private");
});

describe("weekly briefing", () => {
  it("shows the week's figures and saves a regret answer", async () => {
    render(<App />);
    expect(await screen.findByText("Gym Club")).toBeInTheDocument();
    expect(screen.getByText("£1,234.00")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Not worth it" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url === "/api/briefing/regret/7")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.method === "POST")?.body ?? "{}")).toEqual({ worth_it: false });
    await waitFor(() => expect(screen.getByRole("button", { name: "Not worth it" })).toHaveAttribute("aria-pressed", "true"));
  });

  it("Hide amounts blurs every figure on both pages", async () => {
    localStorage.setItem("fa-private", "1");
    for (const hash of ["#briefing", "#know"]) {
      window.location.hash = hash;
      const { container, unmount } = render(<App />);
      await screen.findByRole("heading", { level: 1 });
      await waitFor(() => expect(container.querySelector("main .money, main h2")).not.toBeNull());
      await screen.findAllByRole("heading", { level: 2 });
      await new Promise((r) => setTimeout(r, 50));
      const main = container.querySelector("main")?.cloneNode(true) as HTMLElement;
      main.querySelectorAll(".money").forEach((n) => n.remove());
      expect(main.textContent).not.toMatch(/£\d/);
      unmount();
    }
  });
});

describe("How well I know you", () => {
  it("each reset calls its wipe endpoint after a confirmation", async () => {
    window.location.hash = "#know";
    render(<App />);
    await screen.findByText("Which insights are useful");
    const cases: [RegExp, string][] = [
      [/Reset: insight ratings/, "/api/insights/feedback"],
      [/Reset: worth-it answers/, "/api/briefing/regret"],
      [/Reset: assistant feedback/, "/api/feedback"],
    ];
    for (const [name, url] of cases) {
      await userEvent.click(screen.getByRole("button", { name }));
      expect(calls.some((c) => c.method === "DELETE" && c.url === url)).toBe(false); // not on the first tap
      await userEvent.click(screen.getByRole("button", { name: /^Yes, reset/ }));
      await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url === url)).toBe(true));
    }
  });

  it("a memory is forgotten through its own delete", async () => {
    window.location.hash = "#know";
    render(<App />);
    const row = (await screen.findByText(/Saving/)).closest("li") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: /Forget/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/memories/3")).toBe(true));
  });
});
