import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
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
  it("icon-only controls keep their names, and Hide amounts hints its H shortcut", () => {
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "#settings");
    const eye = screen.getByRole("button", { name: "Hide amounts" });
    expect(eye).toHaveAttribute("aria-keyshortcuts", "H");
    expect(eye).toHaveAttribute("data-tip", "Hide amounts · H");
    expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
  });

  it("More opens a menu of Hide amounts and Settings; Escape closes it and returns focus", async () => {
    const more = screen.getByRole("button", { name: "More" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(more);
    expect(more).toHaveAttribute("aria-expanded", "true");
    const menu = document.getElementById(more.getAttribute("aria-controls")!)!;
    const hide = [...menu.querySelectorAll("button")].find((b) => b.textContent === "Hide amounts")!;
    await userEvent.click(hide);
    expect(hide).toHaveAttribute("aria-pressed", "true");
    expect(document.documentElement).toHaveAttribute("data-private");
    expect(menu.querySelector("a[href='#settings']")).toHaveTextContent("Settings");

    await userEvent.keyboard("{Escape}");
    expect(more).toHaveAttribute("aria-expanded", "false");
    expect(more).toHaveFocus();
  });

  it("a press outside closes the menu", async () => {
    await userEvent.click(screen.getByRole("button", { name: "More" }));
    await userEvent.click(document.body);
    expect(screen.getByRole("button", { name: "More" })).toHaveAttribute("aria-expanded", "false");
  });
});
