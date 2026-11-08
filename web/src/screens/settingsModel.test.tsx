import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Settings } from "./Settings";

type Call = { url: string; method: string; body: unknown };

function api(model: string) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ url, method, body });
      if (url === "/api/chat/model") return Response.json(method === "PUT" ? body : { model });
      if (url === "/api/merchants") return Response.json({ items: [] });
      return new Response("{}", { status: 404 });
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("Settings: Assistant model", () => {
  it("shows the saved choice and saves a change", async () => {
    const calls = api("fast");
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Settings />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("radio", { name: /Faster/ })).toBeChecked();
    await userEvent.click(screen.getByRole("radio", { name: /More careful/ }));

    await waitFor(() => expect(screen.getByRole("radio", { name: /More careful/ })).toBeChecked());
    expect(calls.find((c) => c.method === "PUT")).toEqual({ url: "/api/chat/model", method: "PUT", body: { model: "careful" } });
  });
});
