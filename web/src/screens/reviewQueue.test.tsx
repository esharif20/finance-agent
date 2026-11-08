import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { ReviewQueue } from "./ReviewQueue";

type Queue = components["schemas"]["QueueOut"];
type Item = components["schemas"]["finance_agent__api__review_queue__ItemOut"];

// the shown name follows the stored title unless a test says otherwise
const withShown = (i: Omit<Item, "display_name"> & { display_name?: string }): Item => ({ ...i, display_name: i.display_name ?? i.title });
const item = (over: Partial<Item>): Item => withShown({
  id: "category:1",
  type: "category",
  version: "v1",
  weight: 12,
  title: "Tesco",
  merchant_id: 1,
  transaction_ids: [10, 11],
  choices: [],
  suggestions: [],
  skipped: false,
  spend_minor: 0,
  currency: "GBP",
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
const queue = (items: Item[]): Queue => ({ items, remaining: items.length, ledger_version: 1, applied: 0 });
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

  it("puts the local model's suggestions first and answers nothing by itself", async () => {
    const { calls } = setup(queue([item({ suggestions: [["Transport", "Taxi"]] })]));
    const buttons = await screen.findAllByRole("button", { name: /Taxi|Groceries|Coffee/ });
    expect(buttons[0]).toHaveTextContent("Taxi");
    expect(calls).toHaveLength(0);
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
    expect(invalidate).toHaveBeenCalledWith({ predicate: expect.any(Function) }); // everything but the queue the reply replaced
  });

  it("a held or doubled key answers one item, not the next as well", async () => {
    const { calls } = setup(queue([A, B]), queue([B]));
    await screen.findByRole("button", { name: /Groceries/ });
    fireEvent.keyDown(window, { key: "1" });
    fireEvent.keyDown(window, { key: "1" });
    fireEvent.keyDown(window, { key: "1", repeat: true });
    await screen.findByRole("heading", { name: "Costa" });
    expect(calls).toHaveLength(1);
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

  it("says so when an undo finds the answer already undone elsewhere", async () => {
    const message = "That answer was already undone elsewhere; nothing changed.";
    setup(queue([A]), { ...queue([A]), message });
    await screen.findByRole("heading", { name: "Tesco" });
    await userEvent.keyboard("u");
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(message));
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

  it("a life change shows the API's before and after levels; yes confirms and undo restores", async () => {
    const L = item({
      id: "life_change:rent:2025-03",
      type: "life_change",
      title: "Did something change in March 2025?",
      version: "v9",
      weight: 4,
      merchant_id: null,
      choices: ["yes", "no"],
      before_minor: 85000,
      after_minor: 120000,
    });
    const { calls } = setup(queue([L]), queue([]));
    expect(await screen.findByRole("heading", { name: "Did something change in March 2025?" })).toBeInTheDocument();
    expect(screen.getByText("£850.00")).toBeInTheDocument();
    expect(screen.getByText("£1,200.00")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^Yes/ }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toMatchObject({ url: "/api/review-queue/life_change%3Arent%3A2025-03/answer", body: { version: "v9", choice: "yes" } });
    await screen.findByText("Nothing to review");
    await userEvent.click(screen.getByRole("button", { name: /Undo/ }));
    await waitFor(() => expect(calls[1]).toMatchObject({ url: "/api/review-queue/undo", method: "POST" }));
  });
});

// Writes whose replies the test releases by hand, as a slow server would send them.
function slowSetup(first: Queue) {
  const posts: { url: string; release: (r: Response) => void }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      if ((init?.method ?? "GET") === "GET") {
        return Promise.resolve(new Response(JSON.stringify(url === "/api/categories" ? groups : first)));
      }
      return new Promise<Response>((release) => posts.push({ url, release }));
    }),
  );
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ReviewQueue />
    </QueryClientProvider>,
  );
  return posts;
}
const pause = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe("ReviewQueue while the server is slow", () => {
  it("shows the next item at once and keeps every click, in order", async () => {
    const posts = slowSetup(queue([A, B, C]));
    await userEvent.click(await screen.findByRole("button", { name: /Groceries/ }));
    // before any reply: Tesco is gone and Costa is up
    expect(await screen.findByRole("heading", { name: "Costa" })).toBeInTheDocument();
    await pause(250); // a second, deliberate click, not a double click
    await userEvent.click(screen.getByRole("button", { name: /Skip/ }));
    expect(await screen.findByRole("heading", { name: "Netflix" })).toBeInTheDocument();
    expect(posts.map((p) => p.url)).toEqual(["/api/review-queue/category%3A1/answer"]); // one at a time
    posts[0]!.release(new Response(JSON.stringify(queue([B, C]))));
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1]!.url).toBe("/api/review-queue/category%3A2/skip");
    // the first reply still lists Costa; it stays hidden until its own skip is answered
    expect(screen.getByRole("heading", { name: "Netflix" })).toBeInTheDocument();
    posts[1]!.release(new Response(JSON.stringify(queue([C]))));
    await waitFor(() => expect(screen.getByText("Skipped Costa.")).toBeInTheDocument());
  });

  it("puts the item back when the server refuses the answer", async () => {
    const posts = slowSetup(queue([A, B]));
    await userEvent.click(await screen.findByRole("button", { name: /Groceries/ }));
    expect(await screen.findByRole("heading", { name: "Costa" })).toBeInTheDocument();
    posts[0]!.release(new Response(JSON.stringify({ detail: "the item changed since it was shown" }), { status: 409 }));
    expect(await screen.findByRole("heading", { name: "Tesco" })).toBeInTheDocument(); // the refetched queue
  });
});
