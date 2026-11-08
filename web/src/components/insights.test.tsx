import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Insight } from "@/api/client";
import { PrivacyProvider } from "@/lib/privacy";
import { ForecastChart } from "./ForecastChart";
import { InsightsFeed } from "./InsightsFeed";

// synthetic
const insight = (id: number, over: Partial<Insight> = {}): Insight => ({
  id,
  type: "price_increase",
  key: "Streamly",
  category: "Subscriptions",
  raised_on: "2026-03-01",
  impact_minor: 2400,
  confidence: 70,
  evidence: [1, 2],
  explanation: "Streamly went up; that is about £24.00 a year more.",
  signal: null,
  relevance: { liked: 0, disliked: 0 },
  ...over,
});

const calls: { url: string; method: string; body?: string }[] = [];
let feed: Insight[];
const renderFeed = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PrivacyProvider>
        <InsightsFeed />
      </PrivacyProvider>
    </QueryClientProvider>,
  );

beforeEach(() => {
  calls.length = 0;
  feed = [insight(1), insight(2, { key: "Gym Club", type: "recurring", explanation: "Gym Club repeats monthly." })];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body as string | undefined });
      if (method === "POST" && url.endsWith("/dismiss")) feed = feed.filter((i) => i.id !== 1);
      if (url.startsWith("/api/cash-flow/simulate-purchase"))
        return Response.json({
          status: "ok",
          payday: "2026-04-28",
          minimum: { minor: 12345, currency: "GBP" },
          on: "2026-04-10",
          risk: "below_buffer",
          floor: { minor: 5000, currency: "GBP" },
          have: null,
          need: null,
        });
      return Response.json(method === "POST" ? { id: 1, signal: "up" } : feed);
    }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  document.documentElement.removeAttribute("data-private");
});

describe("InsightsFeed", () => {
  it("shows each Insight with its explanation, evidence and impact", async () => {
    renderFeed();
    expect(await screen.findByText("Streamly")).toBeInTheDocument();
    expect(screen.getByText(/went up; that is about/)).toBeInTheDocument();
    expect(screen.getAllByText(/Fair confidence, from 2 transactions/).length).toBe(2);
    expect(screen.getAllByText("£24.00").length).toBeGreaterThan(0);
  });

  it("rates up and down through the API", async () => {
    renderFeed();
    await userEvent.click(await screen.findByRole("button", { name: "Useful: Streamly" }));
    await userEvent.click(screen.getByRole("button", { name: "Not useful: Gym Club" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST").length).toBe(2));
    const posts = calls.filter((c) => c.method === "POST");
    expect(posts[0]).toMatchObject({ url: "/api/insights/1/feedback", body: '{"liked":true}' });
    expect(posts[1]).toMatchObject({ url: "/api/insights/2/feedback", body: '{"liked":false}' });
  });

  it("dismisses an Insight and it leaves the feed", async () => {
    renderFeed();
    await userEvent.click(await screen.findByRole("button", { name: "Dismiss: Streamly" }));
    await waitFor(() => expect(screen.queryByText("Streamly")).not.toBeInTheDocument());
    expect(calls.some((c) => c.url === "/api/insights/1/dismiss" && c.method === "POST")).toBe(true);
  });

  it("wraps the pounds inside an explanation so Hide amounts blurs them", async () => {
    renderFeed();
    const amount = (await screen.findByText("£24.00", { selector: "p span.money" })) as HTMLElement;
    expect(amount.className).toContain("money");
  });

  it("says so when there is nothing to show", async () => {
    feed = [];
    renderFeed();
    expect(await screen.findByText(/Nothing worth your attention yet/)).toBeInTheDocument();
  });

  it("checks a purchase and shows the minimum balance and the risk", async () => {
    renderFeed();
    await userEvent.click(await screen.findByRole("button", { name: "Check a purchase" }));
    await userEvent.type(screen.getByLabelText(/Price/), "249.99");
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByText(/dip into your buffer/)).toBeInTheDocument();
    expect(screen.getAllByText("£123.45").length).toBeGreaterThan(0);
    const sim = calls.find((c) => c.url.startsWith("/api/cash-flow/simulate-purchase"));
    expect(sim?.url).toContain("amount_minor=24999");
  });

  it("refuses a bad price before asking the API", async () => {
    renderFeed();
    await userEvent.click(await screen.findByRole("button", { name: "Check a purchase" }));
    await userEvent.type(screen.getByLabelText(/Price/), "12.345");
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Enter the price/);
    expect(calls.some((c) => c.url.startsWith("/api/cash-flow"))).toBe(false);
  });
});

describe("ForecastChart", () => {
  const months = [
    { month: "2026-04", point: 100000, lower: 90000, upper: 110000 },
    { month: "2026-05", point: 80000, lower: 60000, upper: 100000 },
  ];
  it("draws the band and line, and its table carries every figure as Money", () => {
    const { container } = render(
      <PrivacyProvider>
        <ForecastChart months={months} />
      </PrivacyProvider>,
    );
    expect(container.querySelectorAll("svg path").length).toBe(2);
    expect(screen.getByRole("table", { name: "Balance forecast by month" })).toHaveTextContent("£600.00");
    // every pound figure in the chart is a Money (.money), so one switch blurs axes, tooltip and table
    for (const el of container.querySelectorAll("*"))
      if (el.children.length === 0 && /£\d/.test(el.textContent ?? "") && !el.closest(".money"))
        throw new Error(`unblurred figure: ${el.textContent}`);
  });
  it("renders nothing without months", () => {
    const { container } = render(<ForecastChart months={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
