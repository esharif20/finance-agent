import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { RateNeeded } from "@/components/FxNote";

// synthetic. #187: a rate is entered from the "Rate needed" line, refused here before it is sent
// when the API would refuse it, and every total is refetched once it is saved.

type FxMonth = components["schemas"]["FxMonthOut"];
const needs: FxMonth = {
  month: "2026-01",
  rates: [],
  rate_needed: [
    { currency: "USD", count: 2 },
    { currency: "EUR", count: 1 },
  ],
};

afterEach(() => vi.unstubAllGlobals());

function setup(fetchImpl: () => Promise<Response>) {
  const fetchMock = vi.fn(fetchImpl);
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient();
  const invalidate = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <RateNeeded fx={needs} />
    </QueryClientProvider>,
  );
  return { fetchMock, invalidate };
}
const ok = () =>
  Promise.resolve(
    new Response(JSON.stringify({ currency: "USD", rate: "0.79", on: "2026-01-01", source: "user" }), { status: 200 }),
  );

describe("rate entry", () => {
  it("opens from the Rate needed line with the currency and the month's first day filled in", async () => {
    setup(ok);
    await userEvent.click(screen.getByRole("button", { name: "Enter a rate for USD" }));
    expect(screen.getByRole("dialog", { name: "Enter a USD rate" })).toBeInTheDocument();
    expect(screen.getByLabelText("Date")).toHaveValue("2026-01-01");
    expect(screen.getByLabelText(/Rate/)).toHaveFocus();
  });

  it.each(["0", "-1", "1e-3", "NaN", "٠.٨", "0.1234567890123"])("refuses %s without sending it", async (bad) => {
    const { fetchMock } = setup(ok);
    await userEvent.click(screen.getByRole("button", { name: "Enter a rate for USD" }));
    await userEvent.type(screen.getByLabelText(/Rate/), bad);
    await userEvent.click(screen.getByRole("button", { name: "Save rate" }));
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByLabelText(/Rate/)).toHaveAttribute("aria-invalid", "true");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends the rate as a string, closes, and refreshes the totals", async () => {
    const { fetchMock, invalidate } = setup(ok);
    await userEvent.click(screen.getByRole("button", { name: "Enter a rate for EUR" }));
    await userEvent.type(screen.getByLabelText(/Rate/), "0.85");
    await userEvent.click(screen.getByRole("button", { name: "Save rate" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/fx/rates");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual({ currency: "EUR", on: "2026-01-01", rate: "0.85" });
    expect(invalidate).toHaveBeenCalled();
  });

  it("shows the API's reason when it refuses and keeps the dialog open", async () => {
    setup(() => Promise.resolve(new Response(JSON.stringify({ detail: "Rate out of range." }), { status: 422 })));
    await userEvent.click(screen.getByRole("button", { name: "Enter a rate for USD" }));
    await userEvent.type(screen.getByLabelText(/Rate/), "5");
    await userEvent.click(screen.getByRole("button", { name: "Save rate" }));
    expect(await screen.findByText("Rate out of range.")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("closes on Escape and gives focus back to the line", async () => {
    setup(ok);
    const open = screen.getByRole("button", { name: "Enter a rate for USD" });
    await userEvent.click(open);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(open).toHaveFocus();
  });
});
