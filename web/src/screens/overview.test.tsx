import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { AccountsCard } from "./AccountsCard";
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
};

const cat = (name: string, minor: number) => ({ name, amount: m(minor) });
const spending: Spending = {
  month: "2026-02",
  currency: "GBP",
  total: m(200000),
  unmatched_refunds: m(0),
  refunds: m(0),
  fees: m(0),
  money_out: m(200000),
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
  masked_number: "••••0001",
  name: "Everyday current",
  kind: "current",
  currency: "GBP",
  bank_balance: m(117979),
  ledger_balance: m(117979),
  gaps: [],
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

  it("says so when nothing was spent", () => {
    render(<SpendingDonut spending={{ ...spending, total: m(0), categories: [] }} />);
    expect(screen.getByText(/no spending this month/i)).toBeInTheDocument();
  });
});

describe("AccountsCard", () => {
  it("marks a reconciled account 'Matches ledger'", () => {
    render(<AccountsCard accounts={[account()]} />);

    const row = screen.getByRole("listitem", { name: /Everyday current/ });
    expect(within(row).getByText("Matches ledger")).toBeInTheDocument();
    expect(row).toHaveTextContent("£1,179.79");
    expect(row).toHaveTextContent("••••0001");
    expect(within(row).queryByText("Find it")).not.toBeInTheDocument();
  });

  it("marks an account with a Gap 'Find it' and explains it on request", async () => {
    render(
      <AccountsCard
        accounts={[
          account({
            ledger_balance: m(116739),
            gaps: [
              { from_date: "2026-02-03", to_date: "2026-02-05", amount: m(1240) },
            ],
          }),
        ]}
      />,
    );

    const row = screen.getByRole("listitem", { name: /Everyday current/ });
    expect(within(row).queryByText("Matches ledger")).not.toBeInTheDocument();
    const find = within(row).getByText("Find it");
    expect(row).toHaveTextContent("£12.40");

    await userEvent.click(find);

    expect(row).toHaveTextContent("3 Feb");
    expect(row).toHaveTextContent("5 Feb");
  });

  it("says there are no accounts yet", () => {
    render(<AccountsCard accounts={[]} />);
    expect(screen.getByText(/fa import/)).toBeInTheDocument();
  });
});

describe("Overview", () => {
  afterEach(() => vi.unstubAllGlobals());

  const january = {
    summary: { ...summary, month: "2026-01", money_in: m(50000), money_out: m(30000), net: m(20000) },
    spending: { ...spending, month: "2026-01", total: m(30000), categories: [cat("Rent", 30000)] },
  };
  const stubApi = () =>
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const jan = url.includes("month=2026-01");
        if (url.startsWith("/api/summary")) return Response.json(jan ? january.summary : summary);
        if (url.startsWith("/api/spending")) return Response.json(jan ? january.spending : spending);
        if (url.startsWith("/api/months")) return Response.json(["2026-02", "2026-01"]);
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
    expect(await screen.findByText("Matches ledger")).toBeInTheDocument();
    expect(await screen.findByText("Housing")).toBeInTheDocument();
  });

  it("switches month and updates every figure", async () => {
    stubApi();
    renderOverview();
    await screen.findByLabelText("Net");
    expect(screen.getByText("February 2026", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next month" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Previous month" }));

    expect(await screen.findByText("£500.00")).toBeInTheDocument(); // January's Money in
    expect(screen.getByLabelText("Net")).toHaveTextContent("+£200.00");
    expect(screen.getByLabelText("Money out")).toHaveTextContent("£300.00");
    expect(await screen.findByText("Rent")).toBeInTheDocument(); // January's only Category
    expect(screen.queryByText("Housing")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous month" })).toBeDisabled();
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
