import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { PrivacyProvider } from "@/lib/privacy";
import { categoryHref, parseHash } from "@/lib/route";
import { CategoryScreen } from "./CategoryScreen";

type Detail = components["schemas"]["CategoryDetailOut"];
const m = (minor: number) => ({ minor, currency: "GBP" });

const detail: Detail = {
  name: "Food & Drink",
  month: "2026-02",
  currency: "GBP",
  total: m(40000),
  subcategories: [
    { name: "Groceries", amount: m(30000) },
    { name: "Eating out", amount: m(10000) },
  ],
  merchants: [{ merchant_id: 7, name: "TESCO", amount: m(25000) }],
  trend: ["2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02"].map((month, i) => ({
    month,
    amount: m(i === 5 ? 40000 : 1000),
  })),
  transactions: [
    {
      id: 1,
      posted_on: "2026-02-03",
      description: "TESCO STORES 1234",
      amount: m(-2550),
      merchant_id: 7,
      merchant: "TESCO",
      subcategory: "Groceries",
    },
  ],
};

afterEach(() => vi.unstubAllGlobals());

function mount(category: string, answer: (url: string) => Response) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => answer(url)));
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PrivacyProvider>
        <CategoryScreen category={category} month="2026-02" />
      </PrivacyProvider>
    </QueryClientProvider>,
  );
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

describe("CategoryScreen", () => {
  it("shows the API's figures for subcategories, Merchants, trend and Transactions", async () => {
    mount("Food & Drink", (url) => (url.startsWith("/api/months") ? json(["2026-02", "2026-01"]) : json(detail)));
    const subs = await screen.findByRole("region", { name: "Subcategories" });
    expect(within(subs).getByText("Groceries")).toBeInTheDocument();
    expect(within(subs).getByText("Eating out")).toBeInTheDocument();
    expect(within(subs).getByText("£300.00")).toBeInTheDocument();
    const merchants = screen.getByRole("region", { name: "Top Merchants" });
    expect(within(merchants).getByText("£250.00")).toBeInTheDocument();
    const trend = screen.getByRole("list", { name: /Monthly spending/ });
    expect(within(trend).getAllByRole("listitem")).toHaveLength(6);
    const txns = screen.getByRole("region", { name: "Transactions" });
    expect(within(txns).getByText("−£25.50")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute("href", "#overview");
  });

  it("keys Top Merchants by id, so two with one name both show", async () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const twins: Detail = {
      ...detail,
      merchants: [
        { merchant_id: 1, name: "CORNER SHOP", amount: m(2000) },
        { merchant_id: 2, name: "CORNER SHOP", amount: m(1000) },
        { merchant_id: null, name: "Unknown", amount: m(500) },
      ],
    };
    mount("Food & Drink", (url) => (url.startsWith("/api/months") ? json(["2026-02"]) : json(twins)));
    const merchants = await screen.findByRole("region", { name: "Top Merchants" });
    expect(within(merchants).getAllByText("CORNER SHOP")).toHaveLength(2);
    expect(errors.mock.calls.flat().join(" ")).not.toMatch(/same key/);
    errors.mockRestore();
  });

  it("explains an empty month", async () => {
    mount("Travel", (url) => (url.startsWith("/api/months") ? json(["2026-02"]) : json({}, 404)));
    expect(await screen.findByText("Nothing in Travel this month")).toBeInTheDocument();
  });
});

describe("routes", () => {
  it("round-trips a category with punctuation", () => {
    expect(parseHash(categoryHref("Food & Drink", "2026-02"))).toEqual({
      name: "category",
      category: "Food & Drink",
      month: "2026-02",
    });
    expect(parseHash("")).toEqual({ name: "overview" });
    expect(parseHash("#category/%E0%A4%A")).toEqual({ name: "overview" });
  });
});
