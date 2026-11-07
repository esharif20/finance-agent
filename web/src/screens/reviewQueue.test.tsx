import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { ReviewQueue } from "./ReviewQueue";

type Queue = components["schemas"]["QueueOut"];
type Item = components["schemas"]["ItemOut"];

const item = (over: Partial<Item>): Item => ({
  id: "category:1",
  type: "category",
  version: "v1",
  weight: 12,
  title: "Tesco",
  merchant_id: 1,
  transaction_ids: [10, 11],
  choices: [],
  skipped: false,
  ...over,
});
const A = item({});
const B = item({ id: "category:2", title: "Costa", version: "v2", weight: 1, merchant_id: 2, transaction_ids: [20] });
const C = item({
  id: "subscription:3",
  type: "subscription",
  title: "Netflix",
  version: "v3",
  choices: ["yes", "no"],
  transaction_ids: [],
});
const queue = (items: Item[]): Queue => ({ items, remaining: items.length, ledger_version: 1 });
const groups = [
  { name: "Food & Drink", subs: ["Groceries", "Coffee"] },
  { name: "Transport", subs: ["Taxi"] },
  { name: "Uncategorised", subs: ["Uncategorised"] },
];

function setup(first: Queue, after?: Queue | (() => Response)) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  let current = first; // what a refetch sees: the queue after a write succeeded
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (method === "GET" && url === "/api/categories") return new Response(JSON.stringify(groups));
      if (method === "GET") return new Response(JSON.stringify(current));
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
      if (typeof after === "function") return after();
      current = after ?? first;
      return new Response(JSON.stringify(current));
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <ReviewQueue />
    </QueryClientProvider>,
  );
  return { calls, invalidate };
}
afterEach(() => vi.unstubAllGlobals());

describe("ReviewQueue", () => {
  it("shows the first item with the remaining count and a keyboard hint", async () => {
    setup(queue([A, B]));
    expect(await screen.findByRole("heading", { name: "Tesco" })).toBeInTheDocument();
    expect(screen.getByText("2 to review")).toBeInTheDocument();
    expect(screen.getByText("12 transactions")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Keyboard shortcuts" })).toBeInTheDocument();
  });

  it("answers for the whole Merchant with key 1, then invalidates everything", async () => {
    const { calls, invalidate } = setup(queue([A, B]), queue([B]));
    await screen.findByRole("button", { name: /Groceries/ });
    await userEvent.keyboard("1");
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({
      url: "/api/review-queue/category%3A1/answer",
      method: "POST",
      body: { version: "v1", category: ["Food & Drink", "Groceries"] },
    });
    expect(await screen.findByRole("heading", { name: "Costa" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Tesco: Groceries");
    expect(invalidate).toHaveBeenCalledWith();
  });

  it("Apply to N similar can be switched to just this one", async () => {
    const { calls } = setup(queue([A]), queue([]));
    await screen.findByRole("heading", { name: "Tesco" });
    expect(screen.getByRole("button", { name: "Apply to 12 similar" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Just this one" }));
    await userEvent.click(await screen.findByRole("button", { name: /Groceries/ }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]?.body).toEqual({ version: "v1", category: ["Food & Drink", "Groceries"], transaction_id: 10 });
  });

  it("skips with S, moves with the arrows and undoes with U", async () => {
    const { calls } = setup(queue([A, B]));
    await screen.findByRole("heading", { name: "Tesco" });
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("heading", { name: "Costa" })).toBeInTheDocument();
    await userEvent.keyboard("{ArrowLeft}");
    await userEvent.keyboard("s");
    await waitFor(() => expect(calls[0]?.url).toBe("/api/review-queue/category%3A1/skip"));
    await userEvent.keyboard("u");
    await waitFor(() => expect(calls[1]?.url).toBe("/api/review-queue/undo"));
  });

  it("offers yes and no for a subscription", async () => {
    const { calls } = setup(queue([C]));
    await userEvent.click(await screen.findByRole("button", { name: /Yes/ }));
    await waitFor(() => expect(calls[0]?.body).toEqual({ version: "v3", choice: "yes" }));
  });

  it("ignores shortcuts while typing", async () => {
    const { calls } = setup(queue([A]));
    await screen.findByRole("heading", { name: "Tesco" });
    await userEvent.click(screen.getByRole("combobox", { name: "Another category" }));
    await userEvent.keyboard("s");
    expect(calls).toHaveLength(0);
  });

  it("says so, calmly, when a stale item is refused", async () => {
    setup(
      queue([A]),
      () => new Response(JSON.stringify({ detail: "the item changed since it was shown" }), { status: 409 }),
    );
    await userEvent.click(await screen.findByRole("button", { name: /Groceries/ }));
    expect(await screen.findByRole("status")).toHaveTextContent("changed");
  });

  it("shows a calm empty state", async () => {
    setup(queue([]));
    expect(await screen.findByRole("heading", { name: "Nothing to review" })).toBeInTheDocument();
  });
});
