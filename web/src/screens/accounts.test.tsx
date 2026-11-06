import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { PrivacyProvider } from "@/lib/privacy";
import { AccountStack } from "./AccountStack";
import { Overview } from "./Overview";

type Account = components["schemas"]["AccountOut"];
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
  ...over,
});
const saver = account({
  id: 2,
  masked_number: "••••0002",
  name: "Rainy day saver",
  bank_balance: m(1000000),
  ledger_balance: m(998760),
  gaps: [{ from_date: "2026-02-03", to_date: "2026-02-05", amount: m(1240) }],
});

describe("AccountStack", () => {
  it("shows an All accounts card and one card per account, masked numbers only", () => {
    render(<AccountStack accounts={[account(), saver]} onSelect={() => {}} />);

    const group = screen.getByRole("group", { name: "Accounts" });
    const cards = within(group).getAllByRole("button");
    expect(cards).toHaveLength(3);
    expect(cards[0]).toHaveTextContent("All accounts");
    expect(cards[0]).toHaveTextContent("£11,179.79"); // sum of the API's balances
    expect(cards[1]).toHaveTextContent("Lloyds");
    expect(cards[1]).toHaveTextContent("••••0001");
    expect(cards[1]).toHaveTextContent("£1,179.79");
    expect(cards[1]).toHaveTextContent("Matches ledger");
    expect(cards[2]).toHaveTextContent("Find it");
    expect(group.querySelector("img, svg:not([aria-hidden])")).toBeNull();
  });

  it("announces the selected card and reports a choice", async () => {
    const onSelect = vi.fn();
    const { rerender } = render(
      <AccountStack accounts={[account(), saver]} onSelect={onSelect} />,
    );
    expect(screen.getByRole("button", { name: /All accounts/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await userEvent.click(screen.getByRole("button", { name: /••••0002/ }));
    expect(onSelect).toHaveBeenLastCalledWith(2);

    rerender(<AccountStack accounts={[account(), saver]} selected={2} onSelect={onSelect} />);
    expect(screen.getByRole("button", { name: /••••0002/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: /••••0001/ })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.getByRole("status")).toHaveTextContent("Showing Lloyds ••••0002");
  });

  it("works from the keyboard", async () => {
    const onSelect = vi.fn();
    render(<AccountStack accounts={[account(), saver]} onSelect={onSelect} />);
    await userEvent.tab();
    await userEvent.tab();
    await userEvent.keyboard("{Enter}");
    expect(onSelect).toHaveBeenLastCalledWith(1);
    await userEvent.tab();
    await userEvent.keyboard(" ");
    expect(onSelect).toHaveBeenLastCalledWith(2);
  });

  it("explains a Gap for the accounts in view", async () => {
    render(<AccountStack accounts={[account(), saver]} selected={1} onSelect={() => {}} />);
    expect(screen.queryByText(/Import the statement/)).not.toBeInTheDocument();

    render(<AccountStack accounts={[account(), saver]} selected={2} onSelect={() => {}} />);
    const note = screen.getByText(/Import the statement/);
    expect(note).toHaveTextContent("3 Feb");
    expect(note).toHaveTextContent("£12.40");
  });

  it("blurs and hides card balances in privacy mode", () => {
    localStorage.setItem("fa-private", "1");
    try {
      const { container } = render(
        <PrivacyProvider>
          <AccountStack accounts={[account(), saver]} onSelect={() => {}} />
        </PrivacyProvider>,
      );
      const amounts = container.querySelectorAll(".money");
      expect(amounts.length).toBeGreaterThanOrEqual(3);
      for (const a of amounts) expect(a.querySelector(".sr-only")).toHaveTextContent("Amount hidden");
    } finally {
      localStorage.removeItem("fa-private");
      document.documentElement.removeAttribute("data-private");
    }
  });

  it("says there are no accounts yet", () => {
    render(<AccountStack accounts={[]} onSelect={() => {}} />);
    expect(screen.getByText(/fa import/)).toBeInTheDocument();
  });
});

describe("Overview account filter", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("asks the API for the chosen account's figures", async () => {
    const calls: string[] = [];
    const summary = (net: number) => ({
      month: "2026-02",
      currency: "GBP",
      money_in: m(net + 100),
      money_out: m(100),
      net: m(net),
      gaps: [],
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
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        calls.push(url);
        if (url.startsWith("/api/summary"))
          return Response.json(summary(url.includes("account_id=2") ? 5000 : 9000));
        if (url.startsWith("/api/spending")) return Response.json(spending);
        if (url.startsWith("/api/months")) return Response.json(["2026-02"]);
        return Response.json([account(), saver]);
      }),
    );
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Overview />
      </QueryClientProvider>,
    );
    expect(await screen.findByLabelText("Net")).toHaveTextContent("+£90.00");

    await userEvent.click(screen.getByRole("button", { name: /••••0002/ }));

    expect(await screen.findByText("+£50.00")).toBeInTheDocument();
    expect(calls.some((u) => u.startsWith("/api/spending") && u.includes("account_id=2"))).toBe(
      true,
    );
  });
});
