import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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
  document.documentElement.removeAttribute("data-private");
});
afterEach(() => vi.unstubAllGlobals());

const ask = async (q = "Groceries?") => {
  await userEvent.type(screen.getByLabelText("Message the Assistant"), q);
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
};

describe("Assistant", () => {
  it("streams a reply, labels the path and wraps figures for Hide amounts", async () => {
    fetchMock.mockResolvedValueOnce(stream({ path: "cache", proposal_id: null }, "You spent £42.50 on Groceries."));
    ui();
    await ask();
    expect(await screen.findByText(/Answered by: Cache/)).toBeInTheDocument();
    const fig = screen.getByText("£42.50");
    expect(fig).toHaveClass("money");
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
      "I couldn't check my answer, so here are the figures it was based on.\n\n| Item | Value | Evidence |\n| --- | --- | --- |\n| Groceries | £42.50 | t1 |";
    fetchMock.mockResolvedValueOnce(stream({ path: "deterministic", proposal_id: null }, reply));
    ui();
    await ask();
    const table = await screen.findByRole("table");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Item", "Value", "Evidence"]);
    expect(within(table).getByText("£42.50")).toHaveClass("money");
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

  it("shows a plain error when the Assistant is unavailable", async () => {
    fetchMock.mockResolvedValueOnce(new Response("", { status: 502 }));
    ui();
    await ask();
    expect(await screen.findByRole("alert")).toHaveTextContent(/isn't available/);
  });
});
