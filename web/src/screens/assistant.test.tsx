import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { sumMinor } from "@/lib/chat";
import { INTERRUPTED, resetChatStore } from "@/lib/chatStore";
import { PrivacyProvider } from "@/lib/privacy";
import { Assistant } from "./Assistant";

// synthetic
const ev = (name: string, data: object) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;
const stream = (meta: object, text: string) =>
  new Response(
    [ev("meta", { thread_id: "a".repeat(32), ...meta }), ...text.split(/(?<= )/).map((t) => ev("delta", { text: t })), ev("done", {})].join(""),
    { status: 200, headers: { "content-type": "text/event-stream" } },
  );
const json = (b: object) => new Response(JSON.stringify(b), { status: 200 });

const ui = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PrivacyProvider>
        <Assistant />
      </PrivacyProvider>
    </QueryClientProvider>,
  );

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  localStorage.clear();
  sessionStorage.clear();
  resetChatStore();
  document.documentElement.removeAttribute("data-private");
});
afterEach(() => vi.unstubAllGlobals());

const ask = async (q = "Groceries?") => {
  await userEvent.type(screen.getByLabelText("Message the Assistant"), q);
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
};

describe("Assistant", () => {
  it("warms the local model once when the chat opens, without waiting on it", () => {
    const beacon = vi.fn(() => true);
    vi.stubGlobal("navigator", { ...navigator, sendBeacon: beacon });
    ui();
    expect(beacon).toHaveBeenCalledTimes(1);
    expect(beacon).toHaveBeenCalledWith("/api/chat/warm");
    expect(fetchMock).not.toHaveBeenCalled(); // the first turn's fetch stays the first
  });

  it("streams a reply, labels the path and wraps figures for Hide amounts", async () => {
    fetchMock.mockResolvedValueOnce(stream({ path: "cache", proposal_id: null }, "You spent £42.50 on Groceries."));
    ui();
    await ask();
    expect(await screen.findByText(/Answered by: Cache/)).toBeInTheDocument();
    const fig = screen.getByText("£42.50");
    expect(fig.closest(".money")).not.toBeNull();
    expect(JSON.parse(fetchMock.mock.calls[0]![1].body)).toEqual({ message: "Groceries?" });
  });

  it("sends the thread id on the next turn", async () => {
    fetchMock.mockResolvedValueOnce(stream({ path: "local_model", proposal_id: null }, "Hello."));
    fetchMock.mockResolvedValueOnce(stream({ path: "local_model", proposal_id: null }, "Again."));
    ui();
    await ask("one");
    await screen.findByText("Hello.");
    await ask("two");
    await screen.findByText("Again.");
    expect(JSON.parse(fetchMock.mock.calls[1]![1].body)).toEqual({ message: "two", thread_id: "a".repeat(32) });
  });

  it("renders the fallback table as a table, with figures blurrable", async () => {
    const reply =
      "I couldn't check my answer, so here are the figures it was based on.\n\n| Item | Value | Evidence |\n| --- | --- | --- |\n| spending_summary 2026-04 money_in | £42.50 | t1 |";
    fetchMock.mockResolvedValueOnce(stream({ path: "deterministic", proposal_id: null }, reply));
    ui();
    await ask();
    const table = await screen.findByRole("table");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Item", "Value", "Evidence"]);
    expect(within(table).getByText("April 2026 · Money in")).toBeInTheDocument();
    expect(within(table).getByText("£42.50").closest(".money")).not.toBeNull();
    expect(screen.getByText(/Answered by: Table/)).toBeInTheDocument();
  });

  it("never renders model text as HTML", async () => {
    fetchMock.mockResolvedValueOnce(stream({ path: "local_model", proposal_id: null }, "<img src=x onerror=alert(1)> <b>bold</b>"));
    const { container } = ui();
    await ask();
    await screen.findByText(/<b>bold<\/b>/);
    expect(container.querySelector("img, b")).toBeNull();
  });

  it("confirms a Proposal through the resume endpoint", async () => {
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/api/chat") return stream({ path: "local_model", proposal_id: 7 }, "Here's the change I'd make.");
      if (url === "/api/proposals/7")
        return json({ id: 7, kind: "memory", status: "pending", memory: { type: "fact", text: "Rent is due on the 1st", tags: [], quote: "rent is due on the 1st", said_on: "2026-03-01" }, correction: null });
      if (url.endsWith("/resume")) return stream({ path: "deterministic", proposal_id: null }, `Done: ${JSON.parse(String(init?.body)).decision}.`);
      throw new Error(url);
    });
    ui();
    await ask("remember rent");
    expect(await screen.findByText("Rent is due on the 1st")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("Done: confirm.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Confirmed"));
    expect(screen.queryByRole("button", { name: "Reject" })).toBeNull();
    expect(fetchMock).toHaveBeenCalledWith(`/api/chat/${"a".repeat(32)}/resume`, expect.anything());
  });

  it("keeps the question and the pending answer across a screen change, then finishes", async () => {
    let push!: (s: string) => void;
    let close!: () => void;
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        const enc = new TextEncoder();
        push = (s) => c.enqueue(enc.encode(s));
        close = () => c.close();
      },
    });
    fetchMock.mockResolvedValueOnce(new Response(body, { status: 200 }));
    const first = ui();
    await ask("How much did I spend on fuel last month?");
    push(ev("stage", { text: "Finding your fuel spending…" }));
    expect(await screen.findByText("Finding your fuel spending…")).toBeInTheDocument();
    first.unmount(); // the router swaps in the Review screen

    push(ev("meta", { thread_id: "b".repeat(32), path: "local_model", proposal_id: null }));
    push(ev("delta", { text: "You spent £84.00 on fuel." }));
    close();

    ui(); // back to the Assistant
    expect(screen.getByText("How much did I spend on fuel last month?")).toBeInTheDocument();
    expect(await screen.findByText("£84.00")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1); // the same request, not a second one
  });

  it("restores the conversation after a reload, and marks a cut-off turn", async () => {
    fetchMock.mockResolvedValueOnce(stream({ path: "local_model", proposal_id: null }, "Hello."));
    const first = ui();
    await ask("one");
    await screen.findByText("Hello.");
    first.unmount();
    resetChatStore(); // as a reload: state re-read from sessionStorage
    ui();
    expect(screen.getByText("one")).toBeInTheDocument();
    expect(screen.getByText("Hello.")).toBeInTheDocument();

    const saved = JSON.parse(sessionStorage.getItem("fa.assistant.chat")!);
    saved.messages.push({ id: 99, role: "assistant", text: "", streaming: true });
    saved.busy = true;
    sessionStorage.setItem("fa.assistant.chat", JSON.stringify(saved));
    resetChatStore();
    expect(await screen.findByRole("alert")).toHaveTextContent(INTERRUPTED);
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled(); // only because the draft is empty
    await userEvent.type(screen.getByLabelText("Message the Assistant"), "again");
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
  });

  it("starts in a bad storage without failing", () => {
    const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    resetChatStore();
    ui();
    expect(screen.getByText("Ask about your money")).toBeInTheDocument();
    get.mockRestore();
  });

  it("lists the transactions a figure counted, summing exactly to the figure", async () => {
    const counted = [
      {
        evidence_id: "detail:2025-12:Transport > Fuel",
        total_minor: 8400,
        transactions: [
          { id: 9, posted_on: "2025-12-22", merchant: "Tesco Fuel", amount_minor: 3600 },
          { id: 6, posted_on: "2025-12-07", merchant: "Shell", amount_minor: 4800 },
        ],
      },
    ];
    fetchMock.mockResolvedValueOnce(stream({ path: "local_model", proposal_id: null, counted }, "You spent £84.00 on fuel."));
    ui();
    await ask("Fuel last month?");
    const box = await screen.findByTestId("counted");
    await userEvent.click(within(box).getByText("Show transactions (2)"));
    const rows = within(within(box).getByRole("list", { name: "Transactions counted" })).getAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual([expect.stringMatching(/^22 DecTesco Fuel£36\.00/), expect.stringMatching(/^7 DecShell£48\.00/)]);
    const minors = [...box.querySelectorAll("[data-minor]")].map((e) => Number(e.getAttribute("data-minor")));
    expect(minors.reduce((a, b) => a + b, 0)).toBe(counted[0]!.total_minor);
    expect(sumMinor(counted[0]!.transactions)).toBe(8400);
    expect(screen.getByText(/You spent/)).toHaveTextContent("You spent £84.00 on fuel."); // the reply's figure
    expect(within(box).getByText("Total").nextSibling).toHaveTextContent("£84.00");
  });

  it("shows no list for a reply without counted transactions, or a malformed one", async () => {
    fetchMock.mockResolvedValueOnce(
      stream({ path: "local_model", proposal_id: null, counted: [{ evidence_id: "x", total_minor: 1.5, transactions: [] }] }, "Hello."),
    );
    ui();
    await ask();
    await screen.findByText("Hello.");
    expect(screen.queryByTestId("counted")).toBeNull();
  });

  it("shows a plain error when the Assistant is unavailable", async () => {
    fetchMock.mockResolvedValueOnce(new Response("", { status: 502 }));
    ui();
    await ask();
    expect(await screen.findByRole("alert")).toHaveTextContent(/isn't available/);
  });
});
