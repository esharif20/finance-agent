import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { AccountsCard } from "./AccountsCard";
import { MonthCard } from "./MonthCard";
import { Overview } from "./Overview";

type Summary = components["schemas"]["SummaryOut"];
type Account = components["schemas"]["AccountOut"];

const m = (minor: number) => ({ minor, currency: "GBP" });

const summary: Summary = {
  month: "2026-02",
  currency: "GBP",
  money_in: m(341250),
  money_out: m(247560),
  net: m(93690),
  gaps: [],
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

  const renderOverview = () =>
    render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <Overview />
      </QueryClientProvider>,
    );

  it("renders the API's figures", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        Response.json(url.startsWith("/api/summary") ? summary : [account()]),
      ),
    );

    renderOverview();

    expect(await screen.findByLabelText("Net")).toHaveTextContent("+£936.90");
    expect(await screen.findByText("Matches ledger")).toBeInTheDocument();
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
