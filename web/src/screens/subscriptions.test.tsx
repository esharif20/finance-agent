import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "@/App";
import { SubscriptionsCard } from "@/components/SubscriptionsCard";
import { PrivacyProvider } from "@/lib/privacy";
import type { Costs, Entry } from "@/lib/subscriptions";
import { Subscriptions } from "./Subscriptions";

// synthetic
const p = (minor: number) => ({ minor, currency: "GBP" });
const entry = (id: number, name: string, over: Partial<Entry> = {}): Entry => ({
  merchant_id: id,
  merchant: name,
  kind: "subscription",
  group: "Streaming",
  status: "active",
  reason: "known_service",
  source: "detection",
  evidence: { on: "2026-03-01", charge_count: 3, interval: "monthly" },
  created_at: "2026-03-01T00:00:00",
  changed_at: "2026-03-01T00:00:00",
  ...over,
});
const entries: Entry[] = [
  entry(1, "Netflix"),
  entry(2, "Spotify", { reason: "user_confirmed" }),
  entry(3, "Octopus Energy", { kind: "bill", group: "Energy", reason: "pattern" }),
  entry(4, "Gym Club", { group: "Memberships", status: "user_cancelled" }),
  entry(5, "Mystery Box", { status: "suggested", reason: "pattern", group: null, kind: null }),
];
const cost = (id: number, name: string, minor: number | null, over = {}) => ({
  merchant_id: id,
  merchant: name,
  kind: "subscription",
  group: "Streaming",
  status: "active",
  interval: "monthly",
  last_charge_on: "2026-03-01",
  monthly: minor === null ? null : p(minor),
  cost_note: null,
  counted: true,
  ...over,
});
const costs = (over: Partial<Costs> = {}): Costs => ({
  items: [
    cost(1, "Netflix", 1099, {
      flags: [{ code: "price_rise", on: "2026-03-01", before: p(999), after: p(1099) }],
    }),
    cost(2, "Spotify", 1199, {
      trial: { started_on: "2026-02-01", converts_on: "2026-03-03", converted: false },
    }),
    cost(3, "Octopus Energy", 8450, { kind: "bill", group: "Energy" }),
    cost(4, "Gym Club", 3000, { group: "Memberships", status: "cancelled", counted: false }),
    cost(5, "Mystery Box", null, { status: "suggested", counted: false, cost_note: "no_charge" }),
  ],
  totals: [
    { kind: "bill", monthly: p(8450) },
    { kind: "subscription", monthly: p(2298) },
  ],
  groups: [
    { kind: "subscription", group: "Streaming", monthly: p(2298) },
    { kind: "bill", group: "Energy", monthly: p(8450) },
    { kind: "subscription", group: "Memberships", monthly: p(0) },
  ],
  rate_needed: 0,
  flag_count: 1,
  trial_detection: "partial",
  ...over,
});

const calls: { url: string; method: string; body?: string }[] = [];
function serve(c: Costs = costs()) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? "GET", body: init?.body as string | undefined });
      if (url === "/api/subscriptions/costs") return Response.json(c);
      if (url === "/api/subscriptions" && !init?.method) return Response.json(entries);
      if (url.startsWith("/api/merchants")) return Response.json({ items: [{ id: 9, name: "Dropbox" }] });
      return Response.json(entries[0]);
    }),
  );
}
const client = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });
const wrap = (ui: React.ReactNode) =>
  render(
    <QueryClientProvider client={client()}>
      <PrivacyProvider>{ui}</PrivacyProvider>
    </QueryClientProvider>,
  );

beforeEach(() => {
  calls.length = 0;
  localStorage.clear();
});
afterEach(() => {
  vi.unstubAllGlobals();
  document.documentElement.removeAttribute("data-private");
});

describe("Subscriptions screen", () => {
  it("shows Subscriptions and Bills with group totals that match the API", async () => {
    serve();
    wrap(<Subscriptions />);
    const subs = await screen.findByRole("region", { name: "Subscriptions" });
    expect(within(subs).getAllByText("£22.98", { selector: ".sr-only" })).toHaveLength(2); // section and group total
    const streaming = within(subs).getByRole("region", { name: "Streaming, Subscriptions" });
    expect(within(streaming).getByText("Netflix")).toBeInTheDocument();
    expect(within(streaming).getByText("Spotify")).toBeInTheDocument();
    expect(within(streaming).getByText("£22.98", { selector: ".sr-only" })).toBeInTheDocument();
    const bills = screen.getByRole("region", { name: "Bills" });
    expect(within(bills).getAllByText("£84.50", { selector: ".sr-only" })).toHaveLength(3);
    expect(within(bills).getByText("Octopus Energy")).toBeInTheDocument();
  });

  it("says why each entry is on the list, and marks a cancelled one as not counted", async () => {
    serve();
    wrap(<Subscriptions />);
    expect((await screen.findAllByText("Known service")).length).toBe(2);
    expect(screen.getByText("Confirmed by you on 1 Mar 2026")).toBeInTheDocument();
    expect(screen.getAllByText("Detected: 3 monthly charges").length).toBeGreaterThan(0);
    expect(screen.getByText("Cancelled by you")).toBeInTheDocument();
    expect(screen.getByText("Not in the total")).toBeInTheDocument();
  });

  it("shows flags and trials when the API sends them, with the partial-detection note", async () => {
    serve();
    wrap(<Subscriptions />);
    const flags = await screen.findByRole("list", { name: "Flags for Netflix" });
    expect(flags).toHaveTextContent("Price rose from");
    expect(within(flags).getByText("£9.99", { selector: ".sr-only" })).toBeInTheDocument();
    expect(within(flags).getByText("£10.99", { selector: ".sr-only" })).toBeInTheDocument();
    expect(screen.getByText(/Free trial, converts 3 Mar 2026/)).toBeInTheDocument();
    expect(screen.getByText(/1 flag on your list/)).toBeInTheDocument();
    expect(screen.getByText(/trial detection is partial/i)).toBeInTheDocument();
  });

  it("works without flags or trials in the response", async () => {
    const c = costs();
    for (const i of c.items) {
      delete i.flags;
      delete i.trial;
    }
    delete c.flag_count;
    delete c.trial_detection;
    serve(c);
    wrap(<Subscriptions />);
    expect(await screen.findByText("Netflix")).toBeInTheDocument();
    expect(screen.queryByText(/flag/i)).toBeNull();
    expect(screen.queryByText(/trial detection/i)).toBeNull();
  });

  it("offers Manage links and the Cancelled? control, and sends the cancel", async () => {
    serve();
    wrap(<Subscriptions />);
    const link = await screen.findByRole("link", { name: /Manage Netflix/ });
    expect(link).toHaveAttribute("href", "https://www.netflix.com/youraccount");
    expect(screen.queryByRole("button", { name: "I cancelled Gym Club" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "I cancelled Netflix" }));
    expect(calls).toContainEqual(expect.objectContaining({ url: "/api/subscriptions/1/cancel", method: "POST" }));
  });

  it("removes with an undo, and regroups through the group select", async () => {
    serve();
    wrap(<Subscriptions />);
    await userEvent.click(await screen.findByRole("button", { name: "Remove Netflix" }));
    expect(calls).toContainEqual(expect.objectContaining({ url: "/api/subscriptions/1", method: "DELETE" }));
    expect(await screen.findByRole("button", { name: "Undo" })).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Group for Spotify"), "Software");
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.url).toBe("/api/subscriptions/2");
    expect(JSON.parse(patch?.body ?? "{}")).toEqual({ kind: "subscription", group: "Software" });
  });

  it("adds a merchant to a chosen group", async () => {
    serve();
    wrap(<Subscriptions />);
    await userEvent.click(await screen.findByRole("button", { name: "Add one" }));
    await userEvent.selectOptions(await screen.findByLabelText("Merchant to add"), "Dropbox");
    await userEvent.selectOptions(screen.getByLabelText("Group for the new entry"), "Cloud storage");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    const post = calls.find((c) => c.method === "POST" && c.url === "/api/subscriptions");
    expect(JSON.parse(post?.body ?? "{}")).toEqual({ merchant_id: 9, kind: "subscription", group: "Cloud storage" });
  });

  it("asks 'Is this a subscription?' for a detection, and Yes files it in the chosen group", async () => {
    serve();
    wrap(<Subscriptions />);
    const ask = await screen.findByRole("region", { name: "Is Mystery Box a subscription?" });
    await userEvent.selectOptions(within(ask).getByLabelText("Group for Mystery Box"), "AI tools");
    await userEvent.click(within(ask).getByRole("button", { name: "Yes" }));
    const post = calls.find((c) => c.url === "/api/subscriptions/5/confirm");
    expect(JSON.parse(post?.body ?? "{}")).toEqual({ kind: "subscription", group: "AI tools" });
  });

  it("No dismisses a detection as 'not a subscription'", async () => {
    serve();
    wrap(<Subscriptions />);
    const ask = await screen.findByRole("region", { name: "Is Mystery Box a subscription?" });
    await userEvent.click(within(ask).getByRole("button", { name: "No" }));
    expect(calls).toContainEqual(expect.objectContaining({ url: "/api/subscriptions/5/dismiss", method: "POST" }));
  });

  it("Hide amounts blurs every figure, including totals and price rises", async () => {
    serve();
    wrap(<Subscriptions />);
    await screen.findByText("Netflix");
    await userEvent.keyboard("h");
    expect(document.documentElement).toHaveAttribute("data-private");
    expect(screen.queryAllByText(/£\d/, { selector: ".sr-only" })).toEqual([]); // no amount reaches a screen reader
    const money = document.querySelectorAll(".money");
    expect(money.length).toBeGreaterThanOrEqual(8);
    for (const m of money) expect(m.querySelector("[aria-hidden=true]")).not.toBeNull();
  });
});

describe("Overview card", () => {
  it("shows the monthly total, Bills and the flag count from the API", async () => {
    serve();
    wrap(<SubscriptionsCard />);
    expect(await screen.findByText("£22.98", { selector: ".sr-only" })).toBeInTheDocument();
    expect(screen.getByText("£84.50", { selector: ".sr-only" })).toBeInTheDocument();
    expect(screen.getByText("1 flag")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See all" })).toHaveAttribute("href", "#subscriptions");
  });

  it("omits the flag count until the API serves it, and renders nothing for a bad response", async () => {
    const c = costs();
    delete c.flag_count;
    for (const i of c.items) delete i.flags;
    serve(c);
    const { unmount } = wrap(<SubscriptionsCard />);
    await screen.findByText("£22.98", { selector: ".sr-only" });
    expect(screen.queryByText(/flag/)).toBeNull();
    unmount();
    vi.stubGlobal("fetch", vi.fn(async () => Response.json([])));
    const { container } = wrap(<SubscriptionsCard />);
    await new Promise((r) => setTimeout(r, 20));
    expect(container).toBeEmptyDOMElement();
  });

  it("blurs its figures under Hide amounts", async () => {
    serve();
    wrap(<SubscriptionsCard />);
    await screen.findByText("1 flag");
    await userEvent.keyboard("h");
    expect(screen.queryAllByText(/£\d/, { selector: ".sr-only" })).toEqual([]);
    expect(document.querySelectorAll(".money").length).toBe(2);
  });
});

describe("navigation", () => {
  it("has a Subscriptions entry that opens the screen", async () => {
    serve();
    window.location.hash = "#subscriptions";
    render(<App />);
    expect((await screen.findAllByRole("link", { name: /Subscriptions/ })).length).toBeGreaterThan(0);
    expect(await screen.findByRole("heading", { level: 1, name: "Subscriptions" })).toBeInTheDocument();
    window.location.hash = "";
  });
});
