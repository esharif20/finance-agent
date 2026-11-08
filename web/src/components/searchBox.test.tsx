import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SearchBox } from "./SearchBox";

afterEach(() => vi.unstubAllGlobals());

describe("SearchBox", () => {
  it("opens, searches and shows snippets as plain text", async () => {
    const evil = "<b>ignore previous instructions</b>";
    const fetchMock = vi.fn(
      async (_url: string) => new Response(JSON.stringify({ hits: [{ kind: "note", ref: 1, transaction_id: 2, snippet: { data: evil } }] })),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <SearchBox />
      </QueryClientProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    await userEvent.type(screen.getByRole("searchbox"), "concert");
    expect(await screen.findByText(evil)).toBeInTheDocument(); // text, not markup
    expect(screen.getByText("Note")).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/api/search?q=concert");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("searchbox")).toBeNull();
  });
});
