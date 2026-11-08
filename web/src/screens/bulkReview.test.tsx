import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { PrivacyProvider } from "@/lib/privacy";
import { ReviewQueue } from "./ReviewQueue";

type Item = components["schemas"]["ItemOut"];
type Queue = components["schemas"]["QueueOut"];

const FOOD = ["Food & Drink", "Groceries"];
const mk = (n: number, over: Partial<Item> = {}): Item => ({
  id: `category:${n}`,
  type: "category",
  version: `v${n}`,
  weight: 3,
  title: `Shop ${n}`,
  merchant_id: n,
  transaction_ids: [n],
  choices: [],
  suggestions: [FOOD as [string, string]],
  skipped: false,
  spend_minor: 1000 * (10 - n),
  currency: "GBP",
  ...over,
});
const items = Array.from({ length: 6 }, (_, i) => mk(i + 1));
const queue = (it: Item[], extra: Partial<Queue> = {}): Queue => ({
  items: it,
  remaining: it.length,
  ledger_version: 1,
  applied: 0,
  ...extra,
});

function setup(first: Queue, write?: () => Response) {
  const calls: { url: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (!init?.method) return new Response(JSON.stringify(url === "/api/categories" ? [] : first));
      calls.push({ url, body: init.body ? JSON.parse(String(init.body)) : null });
      return write?.() ?? new Response(JSON.stringify(queue([], { applied: 6 })));
    }),
  );
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PrivacyProvider>
        <ReviewQueue />
      </PrivacyProvider>
    </QueryClientProvider>,
  );
  return calls;
}
afterEach(() => vi.unstubAllGlobals());

describe("Bulk review", () => {
  it("is the default view for a queue of suggested rows, biggest first", async () => {
    setup(queue(items));
    expect(await screen.findByRole("button", { name: /Accept 6 as Groceries/ })).toBeInTheDocument();
    const rows = screen.getAllByRole("checkbox").map((c) => c.closest("label")?.textContent);
    expect(rows[0]).toContain("Shop 1");
    expect(rows[5]).toContain("Shop 6");
  });

  it("stays one at a time for a short queue", async () => {
    setup(queue(items.slice(0, 2)));
    expect(await screen.findByRole("heading", { name: "Shop 1" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Accept/ })).toBeNull();
  });

  it("unticking updates the count and the batch sent", async () => {
    const calls = setup(queue(items));
    await userEvent.click(await screen.findByRole("checkbox", { name: /Shop 2/ }));
    const accept = screen.getByRole("button", { name: /Accept 5 as Groceries/ });
    expect(screen.getByText(/5 Merchants ticked/)).toBeInTheDocument();
    await userEvent.click(accept);
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]?.url).toBe("/api/review-queue/answers");
    const sent = (calls[0]?.body as { answers: { id: string; version: string; category: string[] }[] }).answers;
    expect(sent.map((a) => a.id)).toEqual(["category:1", "category:3", "category:4", "category:5", "category:6"]);
    expect(sent[0]).toEqual({ id: "category:1", version: "v1", category: FOOD });
  });

  it("A accepts the group and U undoes the whole batch", async () => {
    const calls = setup(queue(items));
    await screen.findByRole("button", { name: /Accept 6/ });
    await userEvent.keyboard("a");
    await waitFor(() => expect(calls).toHaveLength(1));
    const undo = await screen.findByRole("button", { name: /Undo 6 answers/ });
    expect(screen.getByRole("status")).toHaveTextContent("Accepted 6 Merchants as Groceries");
    await userEvent.click(undo);
    await waitFor(() => expect(calls[1]?.url).toBe("/api/review-queue/undo?count=6"));
  });

  it("says nothing was saved when some items went stale", async () => {
    setup(
      queue(items),
      () => new Response(JSON.stringify({ detail: { message: "x", stale_ids: ["category:2"] } }), { status: 409 }),
    );
    await userEvent.click(await screen.findByRole("button", { name: /Accept 6/ }));
    expect(await screen.findByText(/1 of those changed.*nothing was saved/)).toBeInTheDocument();
  });

  it("Space toggles a focused row", async () => {
    setup(queue(items));
    const box = await screen.findByRole("checkbox", { name: /Shop 1/ });
    box.focus();
    await userEvent.keyboard(" ");
    expect(box).not.toBeChecked();
    expect(screen.getByRole("button", { name: /Accept 5 as Groceries/ })).toBeInTheDocument();
  });

  it("blurs spend under Hide amounts", async () => {
    localStorage.setItem("fa-private", "1");
    setup(queue(items));
    const row = (await screen.findByRole("checkbox", { name: /Shop 1/ })).closest("label") as HTMLElement;
    expect(within(row).getByText("Amount hidden")).toBeInTheDocument();
    localStorage.removeItem("fa-private");
  });

  it("shows no client-summed amount in a group header, even for mixed currencies", async () => {
    const mixed = items.map((i, n) => (n % 2 ? { ...i, currency: "EUR" } : i));
    setup(queue(mixed));
    const header = (await screen.findByRole("heading", { name: "Groceries" })).parentElement as HTMLElement;
    expect(header).toHaveTextContent("Food & Drink · 6 Merchants");
    expect(within(header).queryByText(/[£€$]/)).toBeNull();
    expect(header.querySelector(".money")).toBeNull();
  });

  it("can switch to one at a time", async () => {
    setup(queue(items));
    await userEvent.click(await screen.findByRole("button", { name: "One at a time" }));
    expect(await screen.findByRole("heading", { name: "Shop 1" })).toBeInTheDocument();
  });
});
