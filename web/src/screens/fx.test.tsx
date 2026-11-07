import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { components } from "@/api/schema";
import { AccountStack } from "./AccountStack";
import { MonthCard } from "./MonthCard";

// #156: foreign figures count in GBP with their rate's provenance; without a rate they are
// "rate needed", left out and said so, never guessed.

type Summary = components["schemas"]["SummaryOut"];
type Account = components["schemas"]["AccountOut"];
type FxMonth = components["schemas"]["FxMonthOut"];
type Balances = components["schemas"]["BalancesOut"];

const gbp = (minor: number) => ({ minor, currency: "GBP" });
const summary: Summary = {
  month: "2026-01",
  currency: "GBP",
  money_in: gbp(0),
  money_out: gbp(2000),
  net: gbp(-2000),
  gaps: [],
  transfers_in: gbp(0),
  transfers_out: gbp(0),
  accounts: [],
};
const used: FxMonth = {
  month: "2026-01",
  rates: [{ currency: "EUR", rate: "0.8695652173913043478260869565", on: "2026-01-01", source: "exchange", count: 3 }],
  rate_needed: [],
};

describe("MonthCard FX", () => {
  it("says which foreign transactions need a rate and that the figures leave them out", () => {
    render(
      <MonthCard summary={summary} fx={{ ...used, rates: [], rate_needed: [{ currency: "USD", count: 2 }] }} />,
    );
    const note = screen.getByRole("status");
    expect(note).toHaveTextContent("Rate needed");
    expect(note).toHaveTextContent("2 USD transactions");
    expect(note).toHaveTextContent(/left out/);
  });

  it("explains the rate behind the totals from a button, by keyboard too", async () => {
    render(<MonthCard summary={summary} fx={used} />);
    const why = screen.getByRole("button", { name: "How foreign amounts were converted" });
    expect(why).toHaveAttribute("aria-expanded", "false");
    why.focus();
    await userEvent.keyboard("{Enter}");
    expect(why).toHaveAttribute("aria-expanded", "true");
    const tip = screen.getByRole("note");
    expect(tip).toHaveTextContent("EUR at £0.8696");
    expect(tip).toHaveTextContent("your exchange on 1 Jan 2026");
    expect(tip).toHaveTextContent("3 transactions");
    await userEvent.keyboard("{Escape}");
    expect(why).toHaveAttribute("aria-expanded", "false");
  });

  it("shows nothing about FX in a GBP-only month", () => {
    render(<MonthCard summary={summary} fx={{ ...used, rates: [] }} />);
    expect(screen.queryByRole("button", { name: /converted/ })).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
  });
});

const account = (over: Partial<Account>): Account => ({
  id: 1,
  bank: "Revolut",
  masked_number: "Current",
  name: "Account Current",
  kind: "current",
  currency: "GBP",
  bank_balance: gbp(90000),
  ledger_balance: gbp(90000),
  gaps: [],
  art: null,
  last_import: null,
  source: "csv",
  ...over,
});
const eur = account({
  id: 2,
  masked_number: "Current EUR",
  currency: "EUR",
  bank_balance: { minor: 109200, currency: "EUR" },
  ledger_balance: { minor: 109200, currency: "EUR" },
});

describe("AccountStack FX", () => {
  const cards = () => within(screen.getByRole("group", { name: "Accounts" })).getAllByRole("button");

  it("totals mixed currencies in GBP from the API's conversion", () => {
    const balances: Balances = { total: gbp(184957), accounts: [] };
    render(<AccountStack accounts={[account({}), eur]} balances={balances} onSelect={() => {}} />);
    expect(cards()[0]).toHaveTextContent("£1,849.57");
    expect(cards()[2]).toHaveTextContent("€1,092.00"); // the Account itself stays in euros
  });

  it("shows rate needed, not a guessed total, when a balance has no rate", () => {
    const balances: Balances = {
      total: null,
      accounts: [{ account_id: 2, currency: "EUR", balance: eur.bank_balance!, gbp: null, rate: null }],
    };
    render(<AccountStack accounts={[account({}), eur]} balances={balances} onSelect={() => {}} />);
    expect(cards()[0]).not.toHaveTextContent("£");
    expect(cards()[0]).toHaveTextContent("Total unknown: rate needed for EUR");
  });
});
