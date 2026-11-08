import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PrivacyProvider } from "@/lib/privacy";
import { ForecastPanel } from "./ForecastPanel";

afterEach(() => vi.unstubAllGlobals());

const ui = () => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <PrivacyProvider>
      <ForecastPanel />
    </PrivacyProvider>
  </QueryClientProvider>
);

// synthetic
it("draws the API's band", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json([{ month: "2026-04", point: 60000, lower: 50000, upper: 70000 }])));
  render(ui());
  expect(await screen.findByRole("table", { name: "Spending forecast by month" })).toHaveTextContent("£600.00");
});

it("draws nothing for an empty answer", async () => {
  const fetcher = vi.fn(async () => Response.json([]));
  vi.stubGlobal("fetch", fetcher);
  const { container } = render(ui());
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalled());
  await new Promise((r) => setTimeout(r, 30));
  expect(container).toBeEmptyDOMElement();
});
