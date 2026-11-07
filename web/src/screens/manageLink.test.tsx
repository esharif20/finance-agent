import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { PrivacyProvider } from "@/lib/privacy";
import { CategoryScreen } from "./CategoryScreen";
import { Transactions } from "./Transactions";

type Txn = components["schemas"]["TransactionOut"];
const m = (minor: number) => ({ minor, currency: "GBP" });
const groups = [{ name: "Subscriptions", subs: ["Streaming"] }];
const txn = (over: Partial<Txn> = {}): Txn => ({
  id: 7,
  posted_on: "2026-02-04",
  description: "SPOTIFY P1234",
  amount: m(-1099),
  account_id: 1,
  kind: "Spending",
  merchant_id: 3,
  merchant: "Spotify",
  category: ["Subscriptions", "Streaming"],
  source: "merchant:Spotify",
  ...over,
});

const client = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });
afterEach(() => vi.unstubAllGlobals());

function list(items: Txn[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url.startsWith("/api/transactions")
        ? Response.json({ items, total: items.length })
        : Response.json(url.startsWith("/api/categories") ? groups : []),
    ),
  );
  render(
    <QueryClientProvider client={client()}>
      <Transactions />
    </QueryClientProvider>,
  );
}

describe("Manage link in the Transactions list", () => {
  it("opens the built-in page in a new tab for a known subscription, and only for subscriptions", async () => {
    list([txn({ id: 1 }), txn({ id: 2, merchant: "Tesco", category: ["Food & Drink", "Groceries"] })]);
    const link = await screen.findByRole("link", { name: /Manage Spotify/ });
    expect(link).toHaveAttribute("href", "https://www.spotify.com/account/");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.queryByText("No manage link yet")).toBeNull();
  });

  it("says so for an unknown merchant and ignores any link in the description", async () => {
    list([
      txn({
        merchant: "Obscure Club",
        description: "OBSCURE CLUB https://evil.example/pay",
        category: ["Subscriptions", "Memberships"],
      }),
    ]);
    expect(await screen.findByText("No manage link yet")).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });
});

describe("Manage link in the Category drill-down", () => {
  it("shows Manage on Subscriptions Transactions", async () => {
    const d = {
      name: "Subscriptions",
      month: "2026-02",
      currency: "GBP",
      total: m(1099),
      subcategories: [{ name: "Streaming", amount: m(1099) }],
      merchants: [],
      trend: [{ month: "2026-02", amount: m(1099) }],
      transactions: [
        {
          id: 1,
          posted_on: "2026-02-03",
          description: "NETFLIX.COM",
          amount: m(-1099),
          merchant_id: 7,
          merchant: "Netflix",
          subcategory: "Streaming",
        },
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => Response.json(url.startsWith("/api/months") ? ["2026-02"] : d)),
    );
    render(
      <QueryClientProvider client={client()}>
        <PrivacyProvider>
          <CategoryScreen category="Subscriptions" month="2026-02" />
        </PrivacyProvider>
      </QueryClientProvider>,
    );
    const link = await screen.findByRole("link", { name: /Manage Netflix/ });
    expect(link).toHaveAttribute("href", "https://www.netflix.com/youraccount");
  });
});
