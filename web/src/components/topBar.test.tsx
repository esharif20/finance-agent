import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PrivacyProvider } from "@/lib/privacy";
import { TopBar } from "./TopBar";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ last_sync_at: null }), { status: 200 })));
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PrivacyProvider>
        <TopBar tabs={[{ id: "overview", label: "Overview" }]} tab="overview" settings={false} />
      </PrivacyProvider>
    </QueryClientProvider>,
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  document.documentElement.removeAttribute("data-private");
});

describe("TopBar", () => {
  it("icon-only controls keep their names; the values toggle is labelled and hints its H shortcut", async () => {
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "#settings");
    const eye = screen.getByRole("button", { name: "Hide values" });
    expect(eye).toHaveAttribute("aria-keyshortcuts", "H");
    expect(eye).toHaveAttribute("data-tip", "Hide values · H");
    await userEvent.click(eye);
    expect(document.documentElement).toHaveAttribute("data-private");
    expect(screen.getByRole("button", { name: "Show values" })).toBe(eye);
    expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
  });

  it("locally the mark goes to Overview, since / is the app", () => {
    expect(screen.getByRole("link", { name: "Finance Agent" })).toHaveAttribute("href", "#overview");
  });
});

describe("TopBar in the demo build", () => {
  it("the mark links to the landing page at /", () => {
    cleanup();
    vi.stubEnv("VITE_DEMO", "1");
    render(
      <QueryClientProvider client={new QueryClient()}>
        <PrivacyProvider>
          <TopBar tabs={[]} tab="" settings={false} />
        </PrivacyProvider>
      </QueryClientProvider>,
    );
    expect(screen.getByRole("link", { name: "Finance Agent home" })).toHaveAttribute("href", "/");
    vi.unstubAllEnvs();
  });
});
