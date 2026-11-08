import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { PrivacyProvider } from "@/lib/privacy";
import { MonthCard } from "./MonthCard";
import { SpendingDonut } from "./SpendingDonut";
import { Overview } from "./Overview";

type Summary = components["schemas"]["SummaryOut"];
type Account = components["schemas"]["AccountOut"];
type Spending = components["schemas"]["SpendingOut"];

const m = (minor: number) => ({ minor, currency: "GBP" });

const summary: Summary = {
  month: "2026-02",
  currency: "GBP",
  money_in: m(341250),
  money_out: m(247560),
  net: m(93690),
  gaps: [],
  transfers_in: m(0),
  transfers_out: m(0),
  accounts: [],
};

const cat = (name: string, minor: number) => ({ name, amount: m(minor) });
const txn = (posted_on: string) => ({
  id: 1,
  posted_on,
  description: "SYNTH RENT",
  amount: m(-90000),
  account_id: 1,
  kind: "Spending",
  merchant_id: null,
  merchant: null,
  category: ["Housing", "Rent"],
  source: "rule:rent",
});
const spending: Spending = {
  month: "2026-02",
  currency: "GBP",
  total: m(200000),
  unmatched_refunds: m(0),
  refunds: m(0),
  fees: m(0),
  money_out: m(200000),
  merchants: [],
  categories: [
    cat("Housing", 90000),
    cat("Food & Drink", 40000),
    cat("Shopping", 30000),
    cat("Transport", 20000),
    cat("Travel", 10000),
    cat("Health", 6000),
    cat("Entertainment", 4000),
  ],
};

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
  last_import: null,
  source: "csv",
  ...over,
});

describe("MonthCard", () => {
  it("shows money in, out and net from minor units", () => {
    render(<MonthCard summary={summary} />);

    expect(screen.getByText("February 2026")).toBeInTheDocument();
    expect(screen.getByLabelText("Money in")).toHaveTextContent("£3,412.50");
    expect(screen.getByLabelText("Money out")).toHaveTextContent("£2,475.60");
    expect(screen.getByLabelText("Net")).toHaveTextContent("+£936.90");
  });

  it("draws the pound strip with a text equivalent", () => {
    render(<MonthCard summary={summary} />);

    expect(
      screen.getByRole("img", {
        name: "Of every £1 that came in, 73p went out and 27p stayed.",
      }),
    ).toBeInTheDocument();
  });

  it("says so when more went out than came in", () => {
    render(
      <MonthCard
        summary={{ ...summary, money_out: m(400000), net: m(-58750) }}
      />,
    );

    expect(screen.getByLabelText("Net")).toHaveTextContent("−£587.50");
    expect(screen.getByText(/more went out than came in/i)).toBeInTheDocument();
  });

  it("warns that figures may be incomplete when a Gap touches the month", () => {
    render(
      <MonthCard
        summary={{
          ...summary,
          gaps: [
            { from_date: "2026-02-03", to_date: "2026-02-05", amount: m(1240) },
          ],
        }}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(/may be incomplete/i);
  });
});

describe("SpendingDonut", () => {
  it("lists every Category with its amount and share, colouring only the top 5", () => {
    render(<SpendingDonut spending={spending} />);

    const rows = screen.getAllByRole("row").slice(1); // after the header
    expect(rows).toHaveLength(7);
    const housing = rows[0] as HTMLElement;
    expect(housing).toHaveTextContent("Housing");
    expect(housing).toHaveTextContent("£900.00");
    expect(housing).toHaveTextContent("45%");
    const dot = (row: HTMLElement) => row.querySelector("span[aria-hidden]") as HTMLElement;
    expect(dot(housing).style.background).toBe("var(--cat-housing)");
    expect(dot(rows[4] as HTMLElement).style.background).toBe("var(--cat-travel)");
    expect(dot(rows[5] as HTMLElement).style.background).toBe("var(--cat-other)");
    expect(dot(rows[6] as HTMLElement).style.background).toBe("var(--cat-other)");
  });

  it("describes itself for screen readers by share, and shows the total", () => {
    render(<SpendingDonut spending={spending} />);

    expect(screen.getByRole("img", { name: /Housing 45%, Food & Drink 20%/ })).toBeInTheDocument();
    expect(screen.getByText("Spent").parentElement).toHaveTextContent("£2,000.00");
  });

  it("reconciles Money out from the API's parts and never draws a negative", () => {
    const parts: Spending = {
      ...spending,
      total: m(90708),
      refunds: m(1099),
      fees: m(2298),
      unmatched_refunds: m(-1099),
      money_out: m(94105),
      merchants: [],
      categories: [cat("Housing", 90000), cat("Uncategorised", 2807)],
    };
    // the parts the screen prints must add up to Money out
    expect(parts.total.minor + parts.refunds.minor + parts.fees.minor).toBe(parts.money_out.minor);

    render(<SpendingDonut spending={parts} />);

    // <Money> repeats each amount once for screen readers; collapse that
    const line = (screen.getByTestId("reconcile").textContent ?? "").replace(/(£[\d,.]+)\1/g, "$1");
    expect(line).toBe(
      "Money out £941.05 = £907.08 spending after refunds + £10.99 refunds received + £22.98 fees",
    );
    const row = screen.getByRole("row", { name: /Refunds \(unmatched\)/ });
    expect(row).toHaveTextContent("−£10.99");
    const ring = screen.getByRole("img", { name: /Spending by Category/ });
    expect(ring.querySelectorAll("circle")).toHaveLength(2); // Housing + grey rest; no refund arc
    expect(screen.getByRole("row", { name: /Uncategorised/ })).toHaveTextContent("£28.07");
  });

  it("blurs every amount and reads 'hidden' in privacy mode", async () => {
    localStorage.setItem("fa-private", "1");
    try {
      const { container } = render(
        <PrivacyProvider>
          <SpendingDonut spending={{ ...spending, unmatched_refunds: m(-1099) }} />
        </PrivacyProvider>,
      );

      const amounts = container.querySelectorAll(".money");
      expect(amounts.length).toBeGreaterThan(8); // centre, legend rows, refunds line, reconcile line
      expect(document.documentElement).toHaveAttribute("data-private");
      for (const a of amounts) expect(a.querySelector(".sr-only")).toHaveTextContent("Amount hidden");
      expect(container.querySelector("svg")?.getAttribute("aria-label")).not.toMatch(/£/);
    } finally {
      localStorage.removeItem("fa-private");
      document.documentElement.removeAttribute("data-private");
    }
  });

  it("says so when nothing was spent", () => {
    render(<SpendingDonut spending={{ ...spending, total: m(0), categories: [] }} />);
    expect(screen.getByText(/no spending this month/i)).toBeInTheDocument();
  });
});

describe("Overview", () => {
  afterEach(() => vi.unstubAllGlobals());

  const january = {
    summary: { ...summary, month: "2026-01", money_in: m(50000), money_out: m(30000), net: m(20000) },
    spending: { ...spending, month: "2026-01", total: m(30000), categories: [cat("Rent", 30000)] },
  };
  const series = [
    { month: "2026-01", currency: "GBP", money_in: m(50000), money_out: m(30000) },
    { month: "2026-02", currency: "GBP", money_in: m(341250), money_out: m(247560) },
  ];
  const stubApi = () =>
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const jan = url.includes("month=2026-01");
        if (url.startsWith("/api/summary")) return Response.json(jan ? january.summary : summary);
        if (url.startsWith("/api/spending")) return Response.json(jan ? january.spending : spending);
        if (url.startsWith("/api/month-series")) return Response.json(series);
        if (url.startsWith("/api/months")) return Response.json(["2026-02", "2026-01"]);
        if (url.startsWith("/api/transactions")) return Response.json({ items: [], total: 0 });
        return Response.json([account()]);
      }),
    );

  const renderOverview = () =>
    render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <Overview />
      </QueryClientProvider>,
    );

  it("renders the API's figures", async () => {
    stubApi();

    renderOverview();

    expect(await screen.findByLabelText("Net")).toHaveTextContent("+£936.90");
    expect((await screen.findAllByText("Matches ledger")).length).toBe(2); // All accounts + the one
    expect(await screen.findByText("Housing")).toBeInTheDocument();
  });

  it("switches month and updates every figure", async () => {
    stubApi();
    renderOverview();
    await screen.findByLabelText("Net");
    expect(screen.getByText("February 2026", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next month" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Previous month" }));

    expect((await screen.findAllByText("£500.00")).length).toBeGreaterThan(0); // January's Money in
    expect(screen.getByLabelText("Net")).toHaveTextContent("+£200.00");
    expect(screen.getByLabelText("Money out")).toHaveTextContent("£300.00");
    expect(await screen.findByText("Rent")).toBeInTheDocument(); // January's only Category
    expect(screen.queryByText("Housing")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous month" })).toBeDisabled();
  });

  // The "click twice" bug: the stepper read the month of the figures on screen, so a second
  // click before the first month's figures landed stepped from the old month again, a no-op.
  it("steps once per click while the figures are still loading, keeping the last ones dimmed", async () => {
    let release = () => {};
    const gate = new Promise<void>((r) => (release = r));
    const december = { ...summary, month: "2025-12", money_in: m(70000), money_out: m(10000), net: m(60000) };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const at = url.match(/month=(\d{4}-\d{2})/)?.[1];
        if (at && at !== "2026-02") await gate; // older months are slow to arrive
        if (url.startsWith("/api/summary")) return Response.json(at === "2025-12" ? december : at === "2026-01" ? january.summary : summary);
        if (url.startsWith("/api/spending")) return Response.json(at === "2026-02" || !at ? spending : { ...january.spending, month: at });
        if (url.startsWith("/api/month-series")) return Response.json(series);
        if (url.startsWith("/api/months")) return Response.json(["2026-02", "2026-01", "2025-12"]);
        if (url.startsWith("/api/transactions")) return Response.json({ items: [], total: 0 });
        return Response.json([account()]);
      }),
    );
    renderOverview();
    expect(await screen.findByLabelText("Net")).toHaveTextContent("+£936.90");
    const prev = screen.getByRole("button", { name: "Previous month" });

    await userEvent.click(prev);
    await userEvent.click(prev);

    // both clicks counted at once, while February's figures stay, marked busy
    expect(screen.getByText("December 2025", { selector: "nav span" })).toBeInTheDocument();
    expect(prev).toBeDisabled();
    expect(screen.getByLabelText("Net")).toHaveTextContent("+£936.90");
    expect(screen.getByLabelText("Net").closest("[aria-busy]")).toHaveAttribute("aria-busy", "true");

    release();
    await vi.waitFor(() => expect(screen.getByLabelText("Net")).toHaveTextContent("+£600.00"));
    expect(screen.getByLabelText("Net").closest("[aria-busy]")).toHaveAttribute("aria-busy", "false");
  });

  it("keeps the last month's transactions on screen until the new month's land, never a blank", async () => {
    let release = () => {};
    const gate = new Promise<void>((r) => (release = r));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const jan = url.includes("month=2026-01");
        if (url.startsWith("/api/summary")) return Response.json(jan ? january.summary : summary);
        if (url.startsWith("/api/spending")) return Response.json(jan ? january.spending : spending);
        if (url.startsWith("/api/month-series")) return Response.json(series);
        if (url.startsWith("/api/months")) return Response.json(["2026-02", "2026-01"]);
        if (url.startsWith("/api/transactions")) {
          if (jan) await gate;
          return Response.json({ items: [{ ...txn(jan ? "2026-01-03" : "2026-02-03"), description: jan ? "SYNTH JAN" : "SYNTH FEB" }], total: 1 });
        }
        if (url.startsWith("/api/categories")) return Response.json([]);
        return Response.json([account()]);
      }),
    );
    renderOverview();
    expect(await screen.findByText(/SYNTH FEB/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Previous month" }));
    await vi.waitFor(() => expect(screen.getByLabelText("Net")).toHaveTextContent("+£200.00"));

    expect(screen.getByText(/SYNTH FEB/)).toBeInTheDocument(); // still there while January loads
    release();
    expect(await screen.findByText(/SYNTH JAN/)).toBeInTheDocument();
  });

  it("picks a month from the comparison bars, by keyboard, and toggles in/out", async () => {
    stubApi();
    renderOverview();
    await screen.findByLabelText("Net");
    const jan = await screen.findByRole("button", { name: /January 2026.*£300\.00/ });
    expect(jan).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: /February 2026/ })).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(screen.getByRole("button", { name: "Money in" }));
    expect(await screen.findByRole("button", { name: /January 2026.*£500\.00/ })).toBeInTheDocument();

    screen.getByRole("button", { name: /January 2026/ }).focus();
    await userEvent.keyboard("{Enter}");

    expect(await screen.findByText("Rent")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /January 2026/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("table", { name: /by month/i })).toBeInTheDocument();
  });

  it("marks the newest month as so far when the Ledger stops before its end", async () => {
    const fetcher = vi.fn(async (url: string) => {
      if (url.startsWith("/api/transactions"))
        return Response.json({ items: [txn("2026-02-14")], total: 1 });
      if (url.startsWith("/api/summary")) return Response.json(summary);
      if (url.startsWith("/api/spending")) return Response.json(spending);
      if (url.startsWith("/api/month-series")) return Response.json(series);
      if (url.startsWith("/api/months")) return Response.json(["2026-02", "2026-01"]);
      if (url.startsWith("/api/transactions")) return Response.json({ items: [], total: 0 });
      return Response.json([account()]);
    });
    vi.stubGlobal("fetch", fetcher);
    renderOverview();
    const feb = await screen.findByRole("button", { name: /February 2026 so far, to 14 Feb/ });
    expect(feb.querySelector("[data-partial]")).not.toBeNull();
    expect(screen.getByRole("button", { name: /January 2026/ }).querySelector("[data-partial]")).toBeNull();
    expect(screen.getByText(/Feb so far, to 14 Feb/)).toBeInTheDocument();
  });

  it("does not mark a month that the Ledger covers to its last day", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/api/transactions"))
          return Response.json({ items: [txn("2026-02-28")], total: 1 });
        if (url.startsWith("/api/summary")) return Response.json(summary);
        if (url.startsWith("/api/spending")) return Response.json(spending);
        if (url.startsWith("/api/month-series")) return Response.json(series);
        if (url.startsWith("/api/months")) return Response.json(["2026-02", "2026-01"]);
        if (url.startsWith("/api/transactions")) return Response.json({ items: [], total: 0 });
        return Response.json([account()]);
      }),
    );
    renderOverview();
    await screen.findByRole("button", { name: /January 2026/ });
    await screen.findByLabelText("Net");
    expect(screen.queryByText(/so far/)).not.toBeInTheDocument();
  });

  it("keeps amounts out of the bar labels in privacy mode", async () => {
    stubApi();
    localStorage.setItem("fa-private", "1");
    try {
      render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <PrivacyProvider>
            <Overview />
          </PrivacyProvider>
        </QueryClientProvider>,
      );
      const jan = await screen.findByRole("button", { name: /January 2026/ });
      expect(jan.getAttribute("aria-label")).toMatch(/amount hidden/);
      expect(jan.getAttribute("aria-label")).not.toMatch(/£/);
    } finally {
      localStorage.removeItem("fa-private");
    }
  });

  it("invites an import when the Ledger is empty", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url.startsWith("/api/summary")
          ? new Response(JSON.stringify({ detail: "none" }), { status: 404 })
          : Response.json([]),
      ),
    );

    renderOverview();

    expect(await screen.findByText(/no transactions yet/i)).toBeInTheDocument();
  });
});
