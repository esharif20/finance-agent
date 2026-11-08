import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { PrivacyProvider } from "@/lib/privacy";
import { ReviewQueue } from "./ReviewQueue";

type Item = components["schemas"]["finance_agent__api__review_queue__ItemOut"];
type Queue = components["schemas"]["QueueOut"];

const FOOD = ["Food & Drink", "Groceries"];
const mk = (n: number, over: Partial<Item> = {}): Item => ({
  id: `category:${n}`,
  type: "category",
  version: `v${n}`,
  weight: 3,
  title: `Shop ${n}`,
  display_name: over.title ?? `Shop ${n}`,
  merchant_id: n,
  transaction_ids: [n],
  choices: [],
  suggestions: [FOOD as [string, string]],
  skipped: false,
  spend_minor: 1000, // equal scores: none sits below the median, so every row starts ticked
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

let gets: string[] = []; // every read since setup
function setup(first: Queue, write?: () => Response) {
  const calls: { url: string; body: unknown }[] = [];
  gets = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (!init?.method) gets.push(url);
      if (url === "/api/review-queue/auto-batch" && autoHold) return autoHold;
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
let autoHold: Promise<Response> | null = null; // holds the auto-batch read open
afterEach(() => {
  autoHold = null;
  vi.unstubAllGlobals();
});

const many = (n: number, over: (i: number) => Partial<Item> = () => ({})) =>
  Array.from({ length: n }, (_, i) => mk(i + 1, { spend_minor: 1000, ...over(i) }));

describe("Bulk review guardrails", () => {
  it("collapses a group over 15 Merchants and blocks Accept until it is expanded", async () => {
    const calls = setup(queue(many(40)));
    const accept = await screen.findByRole("button", { name: /Accept 15 shown/ });
    expect(accept).toHaveAttribute("aria-disabled", "true");
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(screen.getByRole("heading", { name: "Groceries" }).parentElement).toHaveTextContent("40 Merchants · 120 payments");
    await userEvent.click(accept);
    await userEvent.keyboard("a");
    expect(calls).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Show the 40 Merchants" }));
    expect(screen.getAllByRole("checkbox")).toHaveLength(15);
    expect(screen.getByRole("button", { name: /Accept 15 shown \(45 payments\) as Groceries/ })).toHaveAttribute("aria-disabled", "false");
  });

  it("accepts only the 15 shown, then offers the next 15", async () => {
    const all = many(40);
    const calls = setup(queue(all), () => new Response(JSON.stringify(queue(all.slice(15), { applied: 15 }))));
    await userEvent.click(await screen.findByRole("button", { name: "Show the 40 Merchants" }));
    await userEvent.click(screen.getByRole("button", { name: /Accept 15 shown/ }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect((calls[0]?.body as { answers: unknown[] }).answers).toHaveLength(15);
    // the next 15 are now the page shown; Next moves on to the last 10
    expect(await screen.findByText("1 to 15 of 25")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next 15" }));
    expect(screen.getByText("16 to 25 of 25")).toBeInTheDocument();
  });

  it("a group of 15 is open and accepted whole", async () => {
    setup(queue(many(15)));
    expect(await screen.findByRole("button", { name: /Accept 15 merchants \(45 payments\) as Groceries/ })).toHaveAttribute("aria-disabled", "false");
    expect(screen.getAllByRole("checkbox")).toHaveLength(15);
  });

  it("starts Merchants below the group's median unticked, and sends only the ticked", async () => {
    // spends 10..60: the median is 35, so the three smallest start unticked
    const calls = setup(queue(many(6, (i) => ({ spend_minor: (6 - i) * 10 }))));
    const accept = await screen.findByRole("button", { name: /Accept 3 merchants \(9 payments\) as Groceries/ });
    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes.map((b) => b.checked)).toEqual([true, true, true, false, false, false]);
    await userEvent.click(boxes[4]!); // tick one of them
    await userEvent.click(screen.getByRole("button", { name: /Accept 4 merchants/ }));
    await waitFor(() => expect(calls).toHaveLength(1));
    const ids = (calls[0]?.body as { answers: { id: string }[] }).answers.map((a) => a.id);
    expect(ids).toEqual(["category:1", "category:2", "category:3", "category:5"]);
    expect(accept).toBeDefined();
  });
});

describe("Bulk review", () => {
  it("is the default view for a queue of suggested rows, biggest first", async () => {
    setup(queue(items));
    expect(await screen.findByRole("button", { name: /Accept 6 merchants \(18 payments\) as Groceries/ })).toBeInTheDocument();
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
    const accept = screen.getByRole("button", { name: /Accept 5 merchants \(15 payments\) as Groceries/ });
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
    expect(screen.getByRole("button", { name: /Accept 5 merchants \(15 payments\) as Groceries/ })).toBeInTheDocument();
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

  it("a held key answers once: repeats and a second press while pending are ignored", async () => {
    const calls = setup(queue(items));
    await screen.findByRole("button", { name: /Accept 6/ });
    fireEvent.keyDown(window, { key: "a" });
    fireEvent.keyDown(window, { key: "a" }); // a second press before the first has landed
    fireEvent.keyDown(window, { key: "a", repeat: true });
    await screen.findByRole("button", { name: /Undo 6 answers/ });
    expect(calls).toHaveLength(1);
    expect(screen.getByRole("status")).toHaveTextContent("Accepted 6 Merchants as Groceries");
  });

  it("does not fetch the whole queue again after a write; its reply is the queue", async () => {
    const calls = setup(queue(items));
    await userEvent.click(await screen.findByRole("button", { name: /Accept 6/ }));
    await screen.findByRole("button", { name: /Undo 6 answers/ });
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(gets.filter((u) => u === "/api/review-queue")).toHaveLength(1); // the first load only
  });

  it("a refused write does fetch the queue again, to show what changed", async () => {
    setup(queue(items), () => new Response(JSON.stringify({ detail: "Nope." }), { status: 409 }));
    await userEvent.click(await screen.findByRole("button", { name: /Accept 6/ }));
    await screen.findByText(/Nope/);
    await waitFor(() => expect(gets.filter((u) => u === "/api/review-queue")).toHaveLength(2));
  });

  it("keeps Bulk, and its Undo, when the last group goes and only other items remain", async () => {
    const dup = { ...mk(9), id: "possible_duplicate:9", type: "possible_duplicate", suggestions: [], choices: ["keep_both"] };
    setup(queue([...items, dup]), () => new Response(JSON.stringify(queue([dup], { applied: 6 }))));
    await userEvent.click(await screen.findByRole("button", { name: /Accept 6/ }));
    expect(await screen.findByRole("button", { name: /Undo 6 answers/ })).toBeInTheDocument();
    expect(screen.getByText("Nothing left to accept in bulk.")).toBeInTheDocument();
  });

  it("the Auto-categorised line is waited for, so it can't push the list down mid-click", async () => {
    let release: (r: Response) => void = () => {};
    const held = new Promise<Response>((r) => (release = r));
    autoHold = held;
    setup(queue(items));
    await waitFor(() => expect(gets).toContain("/api/review-queue/auto-batch"));
    await waitFor(() => expect(gets).toContain("/api/review-queue"));
    expect(screen.queryByRole("button", { name: /Accept/ })).toBeNull();
    release(new Response(JSON.stringify({ count: 4 })));
    expect(await screen.findByText("Auto-categorised 4 merchants")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Accept 6/ })).toBeInTheDocument();
  });

  it("can switch to one at a time", async () => {
    setup(queue(items));
    await userEvent.click(await screen.findByRole("button", { name: "One at a time" }));
    expect(await screen.findByRole("heading", { name: "Shop 1" })).toBeInTheDocument();
  });
});
