import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { splitLabel } from "@/components/SplitBadge";
import { ReviewQueue } from "./ReviewQueue";
import { Settings, shortcutList } from "./Settings";
import { SpendingDonut } from "./SpendingDonut";
import { TxnRow } from "./TxnRow";

type Txn = components["schemas"]["TransactionOut"];
type Item = components["schemas"]["ItemOut"];
const m = (minor: number) => ({ minor, currency: "GBP" });
const groups = [
  { name: "Food & Drink", subs: ["Groceries", "Coffee"] },
  { name: "Shopping", subs: ["Gifts"] },
  { name: "Uncategorised", subs: ["Uncategorised"] },
];
const txn = (over: Partial<Txn> = {}): Txn => ({
  id: 7,
  posted_on: "2026-02-04",
  description: "TESCO STORES 3021",
  display_name: "Tesco",
  amount: m(-5000),
  account_id: 1,
  kind: "Spending",
  merchant_id: 3,
  merchant: "Tesco",
  category: ["Food & Drink", "Groceries"],
  source: "merchant:Tesco",
  ...over,
});
const merchant = (over: Partial<components["schemas"]["MerchantOut"]> = {}) => ({
  id: 3,
  name: "Tesco",
  default_category: null,
  always_ask: false,
  always_ask_source: null,
  suggest_always_ask: false,
  ...over,
});
const split = (over = {}) => ({
  id: 1,
  transaction_id: 7,
  amount_minor: -5000,
  currency: "GBP",
  needs_recheck: false,
  ledger_version: 1,
  parts: [
    { amount_minor: -3000, category: ["Food & Drink", "Groceries"] },
    { amount_minor: -2000, category: ["Shopping", "Gifts"] },
  ],
  ...over,
});

type Call = { url: string; method: string; body: unknown };
/** A fetch stub: routes are matched on "METHOD url" prefixes, first match wins. */
function api(routes: Record<string, unknown | (() => Response)>) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
      const key = Object.keys(routes).find((k) => `${method} ${url}`.startsWith(k));
      if (key === undefined) return new Response("{}", { status: 404 });
      const r = routes[key];
      if (typeof r === "function") return (r as () => Response)();
      return Response.json(r);
    }),
  );
  return calls;
}
const wrap = (ui: ReactNode) => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>
);
afterEach(() => vi.unstubAllGlobals());

describe("Split badge", () => {
  it("reads Split · N from the number of parts", () => expect(splitLabel(3)).toBe("Split · 3"));

  it("shows on a split payment and expands to the parts, each with a note", async () => {
    api({ "GET /api/splits/7": split(), "GET /api/notes/7": {} });
    render(wrap(<ul><TxnRow t={txn({ source: "split" })} groups={groups} onCategory={vi.fn()} /></ul>));

    const badge = await screen.findByRole("button", { name: /Split · 2/ });
    expect(badge).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(badge);

    const parts = within(await screen.findByRole("list", { name: "Parts" }));
    expect(parts.getByText("Groceries")).toBeInTheDocument();
    expect(parts.getByText("Gifts")).toBeInTheDocument();
    expect(parts.getByLabelText("Note on part 1")).toBeInTheDocument();
    expect(parts.getByLabelText("Note on part 2")).toBeInTheDocument();
  });

  it("flags a Split whose payment changed", async () => {
    api({ "GET /api/splits/7": split({ needs_recheck: true, amount_minor: -5500 }) });
    render(wrap(<ul><TxnRow t={txn({ source: "split" })} groups={groups} onCategory={vi.fn()} /></ul>));
    expect(await screen.findByRole("button", { name: /Split · 2.*Re-check/ })).toBeInTheDocument();
  });

  it("undoes the whole Split in one step", async () => {
    const calls = api({ "GET /api/splits/7": split(), "DELETE /api/splits/7": { id: 4 } });
    render(wrap(<ul><TxnRow t={txn({ source: "split" })} groups={groups} onCategory={vi.fn()} /></ul>));
    await userEvent.click(await screen.findByRole("button", { name: /Split · 2/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Undo split" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/splits/7")).toBe(true));
  });
});

describe("Split editor", () => {
  const open = async () => {
    const calls = api({ "GET /api/notes/7": {}, "GET /api/merchants": { items: [merchant()] }, "PUT /api/splits/7": split() });
    render(wrap(<ul><TxnRow t={txn()} groups={groups} onCategory={vi.fn()} /></ul>));
    await userEvent.click(screen.getByRole("button", { name: /Details for Tesco/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Split this payment" }));
    return { calls, form: within(await screen.findByRole("form", { name: "Split payment" })) };
  };

  it("shows the remainder live and keeps Save off until the parts add up", async () => {
    const { form } = await open();
    const save = form.getByRole("button", { name: "Save split" });
    expect(save).toBeDisabled();

    await userEvent.type(form.getByLabelText("Part 1 amount in pounds"), "30");
    await userEvent.selectOptions(form.getByLabelText("Part 1 category"), "Groceries");
    await userEvent.selectOptions(form.getByLabelText("Part 2 category"), "Gifts");

    expect(form.getByRole("group", { name: /remainder/ })).toHaveTextContent("£20.00");
    expect(save).toBeEnabled();

    await userEvent.clear(form.getByLabelText("Part 1 amount in pounds"));
    await userEvent.type(form.getByLabelText("Part 1 amount in pounds"), "50");
    expect(save).toBeDisabled(); // the last part would be zero
    expect(form.getByRole("status")).not.toHaveTextContent("Nothing is saved until");
    await userEvent.clear(form.getByLabelText("Part 1 amount in pounds"));
    await userEvent.type(form.getByLabelText("Part 1 amount in pounds"), "60");
    expect(form.getByRole("group", { name: /remainder/ })).toHaveTextContent("Over");
    expect(save).toBeDisabled();
  });

  it("sends signed pence and leaves the last amount for the Ledger to fill in", async () => {
    const { calls, form } = await open();
    await userEvent.type(form.getByLabelText("Part 1 amount in pounds"), "30.10");
    await userEvent.selectOptions(form.getByLabelText("Part 1 category"), "Groceries");
    await userEvent.selectOptions(form.getByLabelText("Part 2 category"), "Gifts");
    await userEvent.click(form.getByRole("button", { name: "Save split" }));

    await waitFor(() => expect(calls.find((c) => c.method === "PUT")).toBeDefined());
    expect(calls.find((c) => c.method === "PUT")).toMatchObject({
      url: "/api/splits/7",
      body: {
        parts: [
          { category: ["Food & Drink", "Groceries"], amount_minor: -3010 },
          { category: ["Shopping", "Gifts"] },
        ],
      },
    });
  });

  it("allows at most ten parts and at least two", async () => {
    const { form } = await open();
    expect(form.queryByRole("button", { name: /Remove part/ })).not.toBeInTheDocument();
    for (let i = 0; i < 8; i++) await userEvent.click(form.getByRole("button", { name: "Add part" }));
    expect(form.getByRole("button", { name: "Add part" })).toBeDisabled();
    expect(form.getAllByRole("listitem")).toHaveLength(10);
  });
});

describe("Note field", () => {
  const openNote = async (existing: unknown = null) => {
    const calls = api({
      "GET /api/notes/7": existing ?? (() => new Response("{}", { status: 404 })),
      "PUT /api/notes/7": { text: "x" },
      "DELETE /api/notes/7": { purged: 2 },
      "GET /api/merchants": { items: [merchant()] },
    });
    render(wrap(<ul><TxnRow t={txn()} groups={groups} onCategory={vi.fn()} /></ul>));
    await userEvent.click(screen.getByRole("button", { name: /Details for Tesco/ }));
    return calls;
  };

  it("saves a note of up to 140 characters, counting as you type", async () => {
    const calls = await openNote();
    const box = await screen.findByLabelText("Note");
    await userEvent.type(box, "Birthday gift");
    expect(screen.getByText("13 of 140")).toBeInTheDocument();
    expect(box).toHaveAttribute("maxlength", "140");
    await userEvent.click(screen.getByRole("button", { name: "Save note" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ text: "Birthday gift" }));
  });

  it("warns that backups keep a deleted note, then purges every version", async () => {
    const calls = await openNote({
      transaction_id: 7, part: null, text: "Birthday gift", created_at: "2026-02-04T10:00:00Z", history: [], ledger_version: 1,
    });
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(screen.getByRole("alertdialog", { name: "Delete note" })).toHaveTextContent(/Backups keep a copy until they age out, about a month/);
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Delete every version" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/notes/7")).toBe(true));
  });
});

describe("Always ask", () => {
  it("turns on for the Merchant and says what that did", async () => {
    const calls = api({
      "GET /api/notes/7": {},
      "GET /api/merchants": { items: [merchant({ suggest_always_ask: true })] },
      "PUT /api/merchants/3/always-ask": { message: "Always ask is on: each payment here waits in To review for your answer.", merchant: merchant({ always_ask: true }), switched_off_corrections: [], ledger_version: 2 },
    });
    render(wrap(<ul><TxnRow t={txn()} groups={groups} onCategory={vi.fn()} /></ul>));
    await userEvent.click(screen.getByRole("button", { name: /Details for Tesco/ }));
    const toggle = await screen.findByRole("switch", { name: "Always ask for Tesco" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText(/different Categories/)).toBeInTheDocument();

    await userEvent.click(toggle);

    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ on: true, source: "user" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Always ask is on: each payment here waits in To review"));
  });

  it("marks an unanswered payment as waiting", () => {
    api({});
    render(wrap(<ul><TxnRow t={txn({ category: ["To review", "To review"], source: "always_ask" })} groups={groups} onCategory={vi.fn()} /></ul>));
    expect(screen.getByText("Waiting for your answer")).toBeInTheDocument();
  });
});

describe("Settings", () => {
  it("lists the Always ask Merchants and copies them one per line", async () => {
    api({ "GET /api/merchants": { items: [merchant({ always_ask: true }), merchant({ id: 4, name: "Post Office", always_ask: true }), merchant({ id: 5, name: "Costa" })] } });
    const write = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText: write } });
    render(wrap(<Settings />));

    const list = within(await screen.findByRole("list", { name: "Always ask Merchants" }));
    expect(list.getAllByRole("listitem")).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "Copy list" }));

    expect(write).toHaveBeenCalledWith("Tesco\nPost Office");
    expect(await screen.findByText("Copied 2 Merchants.")).toBeInTheDocument();
    expect(shortcutList([])).toBe("");
  });
});

describe("To review bucket", () => {
  const spending = {
    month: "2026-02",
    currency: "GBP",
    total: m(100000),
    money_out: m(100000),
    refunds: m(0),
    fees: m(0),
    unmatched_refunds: m(0),
    merchants: [],
    categories: [
      { name: "Food & Drink", amount: m(60000) },
      { name: "To review", amount: m(40000) },
    ],
  };

  it("is its own legend row that links to the queue and counts what is waiting", async () => {
    api({ "GET /api/review-queue": { items: [{ id: "always_ask:1", type: "always_ask", version: "v", weight: 1, title: "Tesco", display_name: "Tesco", merchant_id: 3, transaction_ids: [7], choices: [], skipped: false }], remaining: 1, ledger_version: 1 } });
    render(wrap(<SpendingDonut spending={spending} />));
    expect(screen.getByRole("link", { name: "To review" })).toHaveAttribute("href", "#review");
    expect(await screen.findByText("1 waiting")).toBeInTheDocument();
    expect(screen.getByTestId("reconcile")).toHaveTextContent("£1,000.00"); // money out still counts it
  });
});

describe("Review Queue renderers", () => {
  const item = (over: Partial<Item>): Item => ({
    id: "x:1", type: "category", version: "v1", weight: 1, title: "Tesco", display_name: over.title ?? "Tesco", merchant_id: 3, transaction_ids: [7], choices: [], suggestions: [], skipped: false, ...over,
  });
  const queue = (i: Item) => ({ items: [i], remaining: 1, ledger_version: 1 });

  it("always_ask: asks What was this? for this payment, with Split and Note", async () => {
    const calls = api({
      "GET /api/categories": groups,
      "GET /api/review-queue": queue(item({ id: "always_ask:7", type: "always_ask" })),
      "GET /api/notes/7": {},
      "POST /api/review-queue/always_ask%3A7/answer": { items: [], remaining: 0, ledger_version: 2 },
    });
    render(wrap(<ReviewQueue />));
    expect(await screen.findByText("What was this?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Split it" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add a note" }));
    expect(await screen.findByLabelText("Note")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Groceries/ }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ version: "v1", category: ["Food & Drink", "Groceries"] }));
  });

  it("always_ask: Split it reads the one payment, not a search", async () => {
    const calls = api({
      "GET /api/categories": groups,
      "GET /api/review-queue": queue(item({ id: "always_ask:7", type: "always_ask" })),
      "GET /api/transactions/7": txn(),
    });
    render(wrap(<ReviewQueue />));
    await userEvent.click(await screen.findByRole("button", { name: "Split it" }));
    expect(await screen.findByRole("form", { name: "Split payment" })).toBeInTheDocument();
    expect(calls.some((c) => c.url === "/api/transactions/7")).toBe(true);
    expect(calls.some((c) => c.url.startsWith("/api/transactions?"))).toBe(false);
  });

  it("split_recheck: offers Re-split and Undo the split; nothing is rescaled", async () => {
    const calls = api({
      "GET /api/categories": groups,
      "GET /api/review-queue": queue(item({ id: "split_recheck:1", type: "split_recheck", choices: ["revert"] })),
      "GET /api/splits/7": split({ needs_recheck: true, amount_minor: -5500 }),
      "POST /api/review-queue/split_recheck%3A1/answer": { items: [], remaining: 0, ledger_version: 2 },
    });
    render(wrap(<ReviewQueue />));
    expect(await screen.findByText(/Nothing was rescaled/)).toBeInTheDocument();
    const parts = within(await screen.findByRole("list", { name: "Current parts" }));
    expect(parts.getByText("Payment now")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Re-split" }));
    expect(await screen.findByRole("form", { name: "Split payment" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await userEvent.click(screen.getByRole("button", { name: "Undo the split" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ version: "v1", choice: "revert" }));
  });

  it("split_refund: Which part? answers with that part's Category", async () => {
    const calls = api({
      "GET /api/categories": groups,
      "GET /api/review-queue": queue(item({ id: "split_refund:9", type: "split_refund", transaction_ids: [9, 7] })),
      "GET /api/splits/7": split(),
      "POST /api/review-queue/split_refund%3A9/answer": { items: [], remaining: 0, ledger_version: 2 },
    });
    render(wrap(<ReviewQueue />));
    expect(await screen.findByText(/Which part was it for\?/)).toBeInTheDocument();
    expect(screen.getByText(/If it belongs to a different purchase, skip it/)).toBeInTheDocument();
    const group = within(await screen.findByRole("group", { name: "Which part" }));
    expect(group.getAllByRole("button")).toHaveLength(2);

    await userEvent.click(group.getByRole("button", { name: /Gifts/ }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ version: "v1", category: ["Shopping", "Gifts"] }));
  });
});
