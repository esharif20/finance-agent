import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

const m = (minor: number) => ({ minor, currency: "GBP" });
const summary = {
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
const spending = {
  month: "2026-02",
  currency: "GBP",
  total: m(247560),
  categories: [{ name: "Housing", amount: m(247560) }],
  unmatched_refunds: m(0),
  refunds: m(0),
  fees: m(0),
  money_out: m(247560),
  merchants: [],
};
const accounts = [
  {
    id: 1,
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
  },
];

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      Response.json(
        url.startsWith("/api/summary")
          ? summary
          : url.startsWith("/api/spending")
            ? spending
            : url.startsWith("/api/months")
              ? ["2026-02"]
              : url.startsWith("/api/transactions")
                ? { items: [], total: 0 }
                : accounts,
      ),
    ),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  document.documentElement.removeAttribute("data-private");
});

describe("privacy mode", () => {
  it("H hides every amount from screen readers and marks the page private", async () => {
    render(<App />);
    expect(await screen.findByText("+£936.90")).toBeInTheDocument();

    await userEvent.keyboard("h");

    expect(document.documentElement).toHaveAttribute("data-private");
    expect(screen.queryByText("+£936.90")).not.toBeInTheDocument();
    expect(screen.queryByText("£1,179.79")).not.toBeInTheDocument();
    expect(screen.getAllByText("Amount hidden").length).toBeGreaterThanOrEqual(4);
    // the visible (blurred) digits are aria-hidden, so only "Amount hidden" is announced
    const visible = document.querySelectorAll(".money > span:not(.sr-only)");
    expect(visible.length).toBeGreaterThanOrEqual(4);
    for (const el of visible) expect(el).toHaveAttribute("aria-hidden", "true");
  });

  it("chart labels read hidden", async () => {
    render(<App />);
    await screen.findByRole("img", { name: /of every £1/i });

    await userEvent.keyboard("h");

    expect(screen.getByRole("img", { name: "Amounts hidden." })).toBeInTheDocument();
  });

  it("H again shows amounts; the header button mirrors it with aria-pressed", async () => {
    render(<App />);
    const button = await screen.findByRole("button", { name: /hide amounts/i });
    expect(button).toHaveAttribute("aria-pressed", "false");

    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-pressed", "true");
    expect(localStorage.getItem("fa-private")).toBe("1");

    await userEvent.keyboard("H");
    expect(button).toHaveAttribute("aria-pressed", "false");
    expect(document.documentElement).not.toHaveAttribute("data-private");
    expect(await screen.findByText("+£936.90")).toBeInTheDocument();
  });

  it("restores the saved choice on load", async () => {
    localStorage.setItem("fa-private", "1");
    render(<App />);
    await screen.findAllByText("Amount hidden");
    expect(document.documentElement).toHaveAttribute("data-private");
  });

  it("ignores H while typing in a field and with modifiers", async () => {
    render(
      <>
        <App />
        <input aria-label="note" />
      </>,
    );
    await screen.findByText("+£936.90");

    await userEvent.click(screen.getByLabelText("note"));
    await userEvent.keyboard("h");
    expect(document.documentElement).not.toHaveAttribute("data-private");

    await userEvent.click(document.body);
    await userEvent.keyboard("{Meta>}h{/Meta}");
    expect(document.documentElement).not.toHaveAttribute("data-private");
  });
});
