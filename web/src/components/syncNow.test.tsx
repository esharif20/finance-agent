import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { SyncNow } from "./SyncNow";

type Status = components["schemas"]["SyncStatusOut"];

const base: Status = {
  last_sync_at: null,
  last_failure_at: null,
  failure_class: null,
  failures_in_a_row: 0,
  consent_expires_on: null,
  consent_days_left: null,
  consent_warning: false,
  last_backup_at: null,
  backup_warning: false,
};
const NOW = new Date("2026-09-30T12:00:00Z");
const RECENT = { ...base, last_sync_at: "2026-09-30T11:30:00+00:00" };
const STALE = { ...base, last_sync_at: "2026-09-30T10:00:00+00:00" };

function setup(status: Status, sync: () => Response | Promise<Response>) {
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
    init?.method === "POST" ? sync() : new Response(JSON.stringify(status), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <SyncNow now={NOW} />
    </QueryClientProvider>,
  );
  const posts = () => fetchMock.mock.calls.filter(([, i]) => i?.method === "POST").length;
  return { posts, invalidate };
}

const ok = () => new Response(JSON.stringify({ added: 3, matched: 1, already_known: 6 }), { status: 200 });
afterEach(() => vi.unstubAllGlobals());

describe("SyncNow", () => {
  it("syncs on click, shows the counts and refreshes every query", async () => {
    const { posts, invalidate } = setup(RECENT, ok);
    await userEvent.click(await screen.findByRole("button", { name: /sync now/i }));
    expect(await screen.findByText("3 new")).toBeInTheDocument();
    expect(posts()).toBe(1);
    expect(invalidate).toHaveBeenCalled();
  });

  it("shows a syncing state and ignores a second click meanwhile", async () => {
    let release: (r: Response) => void = () => {};
    const { posts } = setup(RECENT, () => new Promise<Response>((r) => (release = r)));
    await userEvent.click(await screen.findByRole("button", { name: /sync now/i }));
    const busy = await screen.findByRole("button", { name: /syncing/i });
    expect(busy).toHaveAttribute("aria-disabled", "true");
    await userEvent.click(busy);
    expect(posts()).toBe(1);
    release(ok());
    expect(await screen.findByText("3 new")).toBeInTheDocument();
  });

  it("shows a calm error with the API's reason", async () => {
    setup(
      RECENT,
      () => new Response(JSON.stringify({ detail: "Sync failed (SimpleFINError)." }), { status: 502 }),
    );
    await userEvent.click(await screen.findByRole("button", { name: /sync now/i }));
    expect(await screen.findByRole("status")).toHaveTextContent("Sync failed (SimpleFINError).");
  });

  it("syncs once on open when the last sync is over an hour old", async () => {
    const { posts } = setup(STALE, ok);
    await waitFor(() => expect(posts()).toBe(1));
    await new Promise((r) => setTimeout(r, 30));
    expect(posts()).toBe(1);
  });

  it("does not sync on open when the last sync is recent", async () => {
    const { posts } = setup(RECENT, ok);
    await screen.findByText(/Synced/);
    await new Promise((r) => setTimeout(r, 30));
    expect(posts()).toBe(0);
  });
});
