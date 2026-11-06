import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { fetchSummary } from "@/api/client";
import { CategoryChip } from "@/components/CategoryChip";
import { Transactions } from "./Transactions";

type Txn = components["schemas"]["TransactionOut"];
const m = (minor: number) => ({ minor, currency: "GBP" });
const groups = [
  { name: "Food & Drink", subs: ["Groceries", "Coffee"] },
  { name: "Transport", subs: ["Taxi"] },
  { name: "Uncategorised", subs: ["Uncategorised"] },
];

const txn = (over: Partial<Txn> = {}): Txn => ({
  id: 7,
  posted_on: "2026-02-04",
  description: "TESCO STORES 3021",
  amount: m(-1250),
  account_id: 1,
  kind: "Spending",
  merchant_id: 3,
  merchant: "Tesco",
  category: ["Food & Drink", "Groceries"],
  source: "merchant:Tesco",
  ...over,
});

describe("CategoryChip", () => {
  const setup = (merchant: string | null = "Tesco") => {
    const onChange = vi.fn();
    render(
      <CategoryChip value={["Food & Drink", "Groceries"]} groups={groups} merchant={merchant} onChange={onChange} />,
    );
    return onChange;
  };

  it("shows the current Category and opens a choice of the rest", async () => {
    setup();
    const chip = screen.getByRole("button", { name: /Category: Food & Drink, Groceries/ });
    expect(chip).toHaveTextContent("Groceries");

    await userEvent.click(chip);

    const dialog = await screen.findByRole("dialog", { name: "Change category" });
    expect(within(dialog).getByRole("button", { name: "Taxi" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Groceries" })).toHaveAttribute("aria-pressed", "true");
  });

  it("saves a pick for this Transaction only by default", async () => {
    const onChange = setup();
    await userEvent.click(screen.getByRole("button", { name: /Category:/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Taxi" }));

    expect(onChange).toHaveBeenCalledWith(["Transport", "Taxi"], false);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("can apply the pick to every Transaction of the Merchant", async () => {
    const onChange = setup();
    await userEvent.click(screen.getByRole("button", { name: /Category:/ }));
    await userEvent.click(await screen.findByRole("checkbox", { name: "Apply to all Tesco" }));
    await userEvent.click(screen.getByRole("button", { name: "Coffee" }));

    expect(onChange).toHaveBeenCalledWith(["Food & Drink", "Coffee"], true);
  });

  it("offers no apply-to-all without a Merchant, and closes on Escape", async () => {
    setup(null);
    await userEvent.click(screen.getByRole("button", { name: /Category:/ }));
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();

    await userEvent.keyboard("{Escape}");

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("choosing the current Category again saves nothing", async () => {
    const onChange = setup();
    await userEvent.click(screen.getByRole("button", { name: /Category:/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Groceries" }));

    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("Transactions", () => {
  afterEach(() => vi.unstubAllGlobals());

  function Totals() {
    const s = useQuery({ queryKey: ["summary"], queryFn: () => fetchSummary() });
    return <p data-testid="out">{s.data?.money_out.minor}</p>;
  }

  it("a Correction posts, then refetches the list and the totals", async () => {
    let category = ["Food & Drink", "Groceries"];
    let out = 1000;
    const posts: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === "POST") {
          posts.push(JSON.parse(init.body as string));
          category = ["Transport", "Taxi"];
          out = 2000;
          return Response.json({ id: 1, ledger_version: 1 }, { status: 201 });
        }
        if (url.startsWith("/api/transactions"))
          return Response.json({ items: [txn({ category })], total: 1 });
        if (url.startsWith("/api/categories")) return Response.json(groups);
        if (url.startsWith("/api/summary")) return Response.json({ money_out: m(out) });
        return Response.json([
          { id: 1, masked_number: "••••0001", name: "Everyday current", kind: "current", currency: "GBP" },
        ]);
      }),
    );
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Totals />
        <Transactions />
      </QueryClientProvider>,
    );
    const row = await screen.findByRole("listitem", { name: /TESCO STORES 3021/ });
    expect(row).toHaveTextContent("Tesco");
    expect(row).toHaveTextContent("£12.50");
    expect(await screen.findByTestId("out")).toHaveTextContent("1000");

    await userEvent.click(within(row).getByRole("button", { name: /Category:/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Taxi" }));

    await waitFor(() => expect(posts).toEqual([{ transaction_id: 7, category: ["Transport", "Taxi"] }]));
    await waitFor(() => expect(screen.getByTestId("out")).toHaveTextContent("2000"));
    expect(await screen.findByRole("button", { name: /Category: Transport, Taxi/ })).toBeInTheDocument();
  });

  it("sends filters and the search term to the API; only Spending and Refund get chips", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        urls.push(url);
        if (url.startsWith("/api/transactions"))
          return Response.json({
            items: [txn({ id: 1 }), txn({ id: 2, kind: "Income", merchant: null, category: null, amount: m(5000) })],
            total: 2,
          });
        return Response.json(url.startsWith("/api/categories") ? groups : []);
      }),
    );
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Transactions />
      </QueryClientProvider>,
    );
    await screen.findAllByRole("listitem");
    expect(screen.getAllByRole("button", { name: /Category:/ })).toHaveLength(1);

    await userEvent.selectOptions(screen.getByLabelText("Kind"), "Income");
    await userEvent.type(screen.getByRole("searchbox"), "tesco");

    await waitFor(() => expect(urls.some((u) => u.includes("kind=Income") && u.includes("q=tesco"))).toBe(true));
  });
});
