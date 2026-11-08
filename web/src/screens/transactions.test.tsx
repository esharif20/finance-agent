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
            items: [
              txn({ id: 1 }),
              txn({ id: 2, kind: "Income", merchant: null, category: null, amount: m(5000) }),
              txn({ id: 3, kind: "Transfer", merchant: null, category: null }),
              txn({ id: 4, kind: "Fee", merchant: null, category: null }),
            ],
            total: 4,
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

  const mountList = (answer: (url: string, init?: RequestInit) => Response | undefined) => {
    let category = ["Food & Drink", "Groceries"];
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push(`${init?.method ?? "GET"} ${url}`);
        const custom = answer(url, init);
        if (custom) return custom;
        if (init?.method === "POST") {
          category = ["Transport", "Taxi"];
          return Response.json({ id: 41, ledger_version: 3 }, { status: 201 });
        }
        if (init?.method === "DELETE") {
          category = ["Food & Drink", "Groceries"];
          return Response.json({ id: 41, ledger_version: 4 });
        }
        if (url.startsWith("/api/transactions")) return Response.json({ items: [txn({ category })], total: 1 });
        if (url.startsWith("/api/categories")) return Response.json(groups);
        return Response.json([]);
      }),
    );
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Transactions />
      </QueryClientProvider>,
    );
    return calls;
  };

  const pickTaxi = async () => {
    const row = await screen.findByRole("listitem", { name: /TESCO STORES 3021/ });
    await userEvent.click(within(row).getByRole("button", { name: /Category:/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Taxi" }));
  };

  it("offers Undo after a change, which reverts that Correction", async () => {
    const calls = mountList(() => undefined);
    await pickTaxi();
    expect(await screen.findByRole("status")).toHaveTextContent("Moved to Taxi");
    await userEvent.click(await screen.findByRole("button", { name: "Undo" }));
    await waitFor(() => expect(calls).toContain("DELETE /api/corrections/41"));
    expect(await screen.findByRole("button", { name: /Category: Food & Drink, Groceries/ })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument());
  });

  it("shows why the Ledger refused a change", async () => {
    mountList((_url, init) =>
      init?.method === "POST"
        ? Response.json({ detail: "Income takes no Category: only Spending and Refunds do" }, { status: 422 })
        : undefined,
    );
    await pickTaxi();
    expect(await screen.findByRole("status")).toHaveTextContent("Income takes no Category");
    expect(screen.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument();
  });
});

describe("Transactions row text and month headers", () => {
  afterEach(() => vi.unstubAllGlobals());
  const mount = (items: Txn[], scope = {}) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/api/transactions")) return Response.json({ items, total: items.length });
        if (url.startsWith("/api/categories")) return Response.json(groups);
        return Response.json([{ id: 1, masked_number: "••••0001", name: "Everyday", kind: "current", currency: "GBP" }]);
      }),
    );
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Transactions scope={scope} />
      </QueryClientProvider>,
    );
  };
  const spot = (id: number, on: string, description = "Spotify P487971923") =>
    txn({ id, posted_on: on, merchant: "Spotify", description });

  it("hides a description that only repeats the Merchant and never leaves a dangling separator", async () => {
    mount([spot(1, "2026-03-04"), spot(2, "2026-02-04", "Spotify Limited")]);
    const row = await screen.findAllByRole("listitem", { name: /Spotify/ });
    expect(row[0]).not.toHaveTextContent("P487971923");
    expect(row[0]).toHaveTextContent("••••0001");
    expect(row[0]?.textContent).not.toMatch(/·\s*$/);
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
  });

  it("adds a month header per month when filtered to a Category", async () => {
    mount([spot(1, "2026-03-04"), spot(2, "2026-03-01"), spot(3, "2026-02-04")], { category: "Food & Drink" });
    expect(await screen.findByText("March 2026")).toBeInTheDocument();
    expect(screen.getAllByText("March 2026")).toHaveLength(1);
    expect(screen.getByText("February 2026")).toBeInTheDocument();
  });
});

describe("CategoryChip placement", () => {
  it("opens towards the left when the right edge has no room", async () => {
    const rect = (left: number, right: number) => ({ left, right, top: 0, bottom: 32, width: right - left, height: 32, x: left, y: 0, toJSON: () => ({}) });
    const spy = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      return this.dataset.slot === "card" ? rect(0, 400) : rect(300, 380);
    });
    render(
      <div data-slot="card">
        <CategoryChip value={["Food & Drink", "Groceries"]} groups={groups} merchant={null} onChange={() => {}} />
      </div>,
    );
    await userEvent.click(screen.getByRole("button", { name: /Category:/ }));
    expect(await screen.findByRole("dialog")).toHaveAttribute("data-align", "end");
    spy.mockRestore();
  });
});
