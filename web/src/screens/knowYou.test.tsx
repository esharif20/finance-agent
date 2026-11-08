import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render as rtlRender, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { KnowYou } from "@/screens/KnowYou";

// synthetic
// A fresh client per render: App's shared one would carry cached answers between tests.
const render = () =>
  rtlRender(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <KnowYou />
    </QueryClientProvider>,
  );

const gbp = (minor: number) => ({ minor, currency: "GBP" });
let calls: { url: string; method: string }[];
let memories: { id: number; text: string; tags: string[] }[];
let memoriesStatus: number;
let deleteStatus: number;
let insufficient: string[];
let regretAnswers: (boolean | null)[];

const briefing = () => ({
  week: "2026-03-02",
  insights: [],
  forecast: null,
  next_payday: null,
  renewals: [],
  regret: regretAnswers.map((w, i) => ({
    transaction_id: i + 1,
    posted_on: "2026-03-01",
    description: "SYNTH CAFE",
    merchant: "Synth Cafe",
    display_name: "Synth Cafe",
    amount: gbp(-1250),
    worth_it: w,
  })),
  insufficient,
});

beforeEach(() => {
  calls = [];
  memories = [{ id: 3, text: "Saving £200 a month for a bike", tags: ["goal"] }];
  memoriesStatus = 200;
  deleteStatus = 200;
  insufficient = [];
  regretAnswers = [true, null, false];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ url, method });
      if (method === "DELETE") return deleteStatus === 200 ? Response.json({ deleted: 1 }) : new Response("{}", { status: deleteStatus });
      if (url.startsWith("/api/briefing")) return Response.json(briefing());
      if (url.startsWith("/api/memories")) return memoriesStatus === 200 ? Response.json({ items: memories }) : new Response("{}", { status: memoriesStatus });
      return Response.json([]);
    }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("How well I know you screen", () => {
  it("shows the three signals, the memory and the answered count", async () => {
    render();
    expect(screen.getByText("Which insights are useful")).toBeInTheDocument();
    expect(screen.getByText("What was worth it")).toBeInTheDocument();
    expect(screen.getByText("Which assistant suggestions you accept")).toBeInTheDocument();
    expect(await screen.findByText(/You have answered 2 this week/)).toBeInTheDocument();
    const row = (await screen.findByText(/Saving/)).closest("li") as HTMLElement;
    expect(within(row).getByText("goal")).toBeInTheDocument();
  });

  it("falls back to generic worth-it copy while the briefing has not loaded", async () => {
    vi.mocked(fetch).mockImplementation(((url: string) =>
      url.startsWith("/api/briefing") ? new Promise(() => {}) : Promise.resolve(Response.json({ items: [] }))) as typeof fetch);
    render();
    expect(await screen.findByText(/Nothing yet/)).toBeInTheDocument();
    expect(screen.getByText(/Your answers to the weekly/)).toBeInTheDocument();
    expect(screen.queryByText(/You have answered/)).toBeNull();
  });

  it("says nothing is remembered when there are no memories", async () => {
    memories = [];
    render();
    expect(await screen.findByText(/Nothing yet/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Forget/ })).toBeNull();
  });

  it("shows a distinct message when memories fail to load", async () => {
    memoriesStatus = 500;
    render();
    expect(await screen.findByText(/Couldn't load your memories/)).toBeInTheDocument();
    expect(screen.queryByText(/Nothing yet/)).toBeNull();
  });

  it("lists what it can't tell yet for each gap reported", async () => {
    insufficient = ["insights", "forecast"];
    render();
    expect(await screen.findByText(/Insights: not enough history/)).toBeInTheDocument();
    expect(screen.getByText(/Forecast: not enough history/)).toBeInTheDocument();
    expect(screen.queryByText(/enough history for insights and the forecast/)).toBeNull();
  });

  it("lists only the forecast gap when only it is reported", async () => {
    insufficient = ["forecast"];
    render();
    expect(await screen.findByText(/Forecast: not enough history/)).toBeInTheDocument();
    expect(screen.queryByText(/Insights: not enough history/)).toBeNull();
  });

  it("says it has enough history when no gap is reported", async () => {
    render();
    expect(await screen.findByText(/enough history for insights and the forecast/)).toBeInTheDocument();
  });

  it("Keep it cancels a reset without calling the wipe endpoint", async () => {
    render();
    await userEvent.click(await screen.findByRole("button", { name: /Reset: insight ratings/ }));
    await userEvent.click(screen.getByRole("button", { name: "Keep it" }));
    expect(screen.getByRole("button", { name: /Reset: insight ratings/ })).toBeInTheDocument();
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("a failed reset shows an alert and a failed forget shows its own", async () => {
    deleteStatus = 500;
    render();
    await userEvent.click(await screen.findByRole("button", { name: /Reset: worth-it answers/ }));
    await userEvent.click(screen.getByRole("button", { name: /^Yes, reset/ }));
    expect(await screen.findByText("Couldn't reset that. Try again.")).toBeInTheDocument();
    const row = (await screen.findByText(/Saving/)).closest("li") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: /Forget/ }));
    expect(await screen.findByText("Couldn't forget that. Try again.")).toBeInTheDocument();
  });

  it("wraps every pound figure in a memory so Hide amounts blurs it", async () => {
    const { container } = render();
    await screen.findByText(/Saving/);
    const root = container.cloneNode(true) as HTMLElement;
    root.querySelectorAll(".money").forEach((n) => n.remove());
    expect(root.textContent).not.toMatch(/£\d/);
    expect(container.querySelectorAll(".money")).toHaveLength(1);
  });
});
