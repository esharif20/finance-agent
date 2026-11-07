import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { categoryHref, parseHash, useRoute } from "@/lib/route";
import { Overview } from "./Overview";

type Account = components["schemas"]["AccountOut"];
type Summary = components["schemas"]["SummaryOut"];
const m = (minor: number) => ({ minor, currency: "GBP" });

const account = (over: Partial<Account> = {}): Account => ({
  id: 1,
  bank: "Lloyds",
  masked_number: "••••0001",
  name: "Everyday current",
  kind: "current",
  currency: "GBP",
  bank_balance: m(117979),
  ledger_balance: m(117979),
  gaps: [],
  art: null,
  last_import: "2026-03-02T09:15:00+00:00",
  source: "csv",
  ...over,
});
const saver = account({
  id: 2,
  bank: "Northbank",
  masked_number: "••••0002",
  name: "Rainy day saver",
  bank_balance: m(1000000),
  ledger_balance: m(998760),
  gaps: [{ from_date: "2026-02-03", to_date: "2026-02-05", amount: m(1240) }],
});

const part = (account_id: number, i: number, o: number, ti: number, to: number) => ({
  account_id,
  money_in: m(i),
  money_out: m(o),
  transfers_in: m(ti),
  transfers_out: m(to),
});
const whole: Summary = {
  month: "2026-02",
  currency: "GBP",
  money_in: m(300000),
  money_out: m(120000),
  net: m(180000),
  gaps: [],
  transfers_in: m(50000),
  transfers_out: m(50000),
  accounts: [part(1, 290000, 115000, 0, 50000), part(2, 10000, 5000, 50000, 0)],
};
const one = (p: ReturnType<typeof part>): Summary => ({
  ...whole,
  ...p,
  net: m(p.money_in.minor - p.money_out.minor),
  accounts: [],
});
const spending = {
  month: "2026-02",
  currency: "GBP",
  total: m(100),
  unmatched_refunds: m(0),
  refunds: m(0),
  fees: m(0),
  money_out: m(100),
  categories: [{ name: "Housing", amount: m(100) }],
  merchants: [{ name: "SYNTH LETTINGS", amount: m(100) }],
};

/** What a screen reader hears: Money prints its figure twice (spoken and drawn). */
function said(el: Element): string {
  const c = el.cloneNode(true) as Element;
  for (const h of c.querySelectorAll("[aria-hidden='true']")) h.remove();
  return (c.textContent ?? "").replace(/\s+/g, " ").trim();
}

function Routed() {
  const r = useRoute();
  return <Overview account={r.name === "overview" ? r.account : undefined} />;
}

function serve() {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      calls.push(url);
      const id = /account_id=(\d+)/.exec(url)?.[1];
      if (url.startsWith("/api/summary"))
        return Response.json(id ? one(whole.accounts[Number(id) - 1] as ReturnType<typeof part>) : whole);
      if (url.startsWith("/api/spending")) return Response.json(spending);
      if (url.startsWith("/api/months")) return Response.json(["2026-02"]);
      if (url.startsWith("/api/month-series")) return Response.json([]);
      if (url.startsWith("/api/transactions")) return Response.json({ items: [], total: 0 });
      return Response.json([account(), saver]);
    }),
  );
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Routed />
    </QueryClientProvider>,
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("routes", () => {
  it("reads the Account from the hash and keeps it on a drill-down", () => {
    expect(parseHash("#account/2")).toEqual({ name: "overview", account: 2 });
    expect(parseHash("#account/x")).toEqual({ name: "overview" });
    expect(parseHash("#overview")).toEqual({ name: "overview" });
    expect(categoryHref("Food & Drink", "2026-02", 2)).toBe("#category/Food%20%26%20Drink/2026-02/2");
    expect(parseHash(categoryHref("Housing", "2026-02", 2))).toEqual({
      name: "category",
      category: "Housing",
      month: "2026-02",
      account: 2,
    });
  });
});

describe("Combined view (All accounts)", () => {
  it("shows each Account's part and the Transfers between them on their own line", async () => {
    serve();
    const table = await screen.findByRole("table", { name: /by account/i });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map(said)).toEqual([
      "Lloyds ••••0001£2,900.00£1,150.00",
      "Northbank ••••0002£100.00£50.00",
    ]);
    expect(said(screen.getByRole("group", { name: "Transfers between your accounts" }))).toBe(
      "£500.00 moved between your accounts, left out of money in and out.",
    );
  });
});

describe("Per-account view", () => {
  it("opens from the card, puts the Account in the hash and Back returns to All accounts", async () => {
    const calls = serve();
    await screen.findByRole("table", { name: /by account/i });

    await userEvent.click(screen.getByRole("button", { name: /••••0002/ }));

    expect(window.location.hash).toBe("#account/2");
    const details = await screen.findByRole("region", { name: "Northbank ••••0002" });
    expect(within(details).getByLabelText("Bank balance")).toHaveTextContent("£10,000.00");
    expect(within(details).getByLabelText("Ledger balance")).toHaveTextContent("£9,987.60");
    expect(within(details).getByRole("status")).toHaveTextContent("One Reconciliation Gap");
    expect(within(details).getByText(/from a CSV statement/)).toBeInTheDocument();
    // its own Transfers, as both legs are this Account's
    expect(said(screen.getByRole("group", { name: "Transfers between your accounts" }))).toContain(
      "£500.00 in, £0.00 out",
    );
    expect(
      calls.some((u) => u.startsWith("/api/transactions") && u.includes("account_id=2") && u.includes("month=2026-02")),
    ).toBe(true);
    expect(screen.getByRole("link", { name: /Housing/ })).toHaveAttribute("href", "#category/Housing/2026-02/2");

    await act(async () => {
      window.location.hash = "#overview"; // what Back does to the hash
    });
    expect(await screen.findByRole("table", { name: /by account/i })).toBeInTheDocument();
  });

  it("opens straight from a reloaded hash", async () => {
    window.location.hash = "#account/1";
    serve();
    expect(await screen.findByRole("region", { name: "Lloyds ••••0001" })).toBeInTheDocument();
    expect(said(screen.getByRole("list", { name: "Top merchants" }))).toBe("SYNTH LETTINGS£1.00");
  });

  it("falls back to All accounts for an Account that isn't there", async () => {
    window.location.hash = "#account/99";
    serve();
    expect(await screen.findByRole("table", { name: /by account/i })).toBeInTheDocument();
    expect(window.location.hash).toBe("#overview");
  });
});
