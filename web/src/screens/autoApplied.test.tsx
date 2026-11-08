import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReviewQueue } from "./ReviewQueue";

const empty = { items: [], remaining: 0, ledger_version: 1, applied: 0 };

function setup(count: number, undo: () => Response) {
  const calls: string[] = [];
  let live = count;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        calls.push(url);
        const res = undo();
        if (res.ok) live = 0;
        return res;
      }
      if (url === "/api/categories") return new Response("[]");
      if (url === "/api/review-queue/auto-batch") return new Response(JSON.stringify({ count: live }));
      return new Response(JSON.stringify(empty));
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

describe("Auto-categorised line", () => {
  it("shows the API's count with an Undo", async () => {
    setup(7, () => new Response("{}"));
    expect(await screen.findByText("Auto-categorised 7 merchants")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Undo all auto-categorising" })).toBeInTheDocument();
  });

  it("is absent when nothing was auto-applied", async () => {
    setup(0, () => new Response("{}"));
    await screen.findByRole("heading", { name: "Nothing to review" });
    expect(screen.queryByText(/Auto-categorised/)).not.toBeInTheDocument();
  });

  it("undoes the batch, confirms, and invalidates the queries", async () => {
    const { calls, invalidate } = setup(
      3,
      () => new Response(JSON.stringify({ ...empty, applied: 3 })),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Undo all auto-categorising" }));
    await waitFor(() => expect(calls).toEqual(["/api/review-queue/undo-auto"]));
    expect(await screen.findByText("Put 3 merchants back in the queue.")).toBeInTheDocument();
    expect(invalidate).toHaveBeenCalledWith({ predicate: expect.any(Function) }); // everything but the queue the reply replaced
    expect(screen.queryByRole("button", { name: "Undo all auto-categorising" })).not.toBeInTheDocument();
  });

  it("says so, and keeps the Undo, when the call fails", async () => {
    setup(2, () => new Response(JSON.stringify({ detail: "try again" }), { status: 500 }));
    await userEvent.click(await screen.findByRole("button", { name: "Undo all auto-categorising" }));
    expect(await screen.findByText(/Nothing was changed/)).toBeInTheDocument();
  });
});
