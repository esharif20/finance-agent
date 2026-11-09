import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { PrivacyProvider } from "@/lib/privacy";
import { BottomBar } from "./BottomBar";

beforeEach(() => {
  render(
    <PrivacyProvider>
      <BottomBar
        tabs={[
          { id: "overview", label: "Overview" },
          { id: "review", label: "Review", badge: 3 },
        ]}
        tab="overview"
        current="overview"
      />
    </PrivacyProvider>,
  );
});
afterEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-private");
});

describe("BottomBar", () => {
  it("switches tabs in the page: pushState and a hashchange, never a navigation", async () => {
    const changes: string[] = [];
    const on = () => changes.push(window.location.hash);
    window.addEventListener("hashchange", on);
    const marker = Symbol("page");
    (window as unknown as Record<symbol, boolean>)[marker] = true;
    await userEvent.click(screen.getByRole("link", { name: /^Review/ }));
    window.removeEventListener("hashchange", on);
    expect(window.location.hash).toBe("#review");
    expect(changes).toEqual(["#review"]);
    // the same window: nothing reloaded
    expect((window as unknown as Record<symbol, boolean>)[marker]).toBe(true);
    window.history.replaceState(null, "", "#");
  });

  it("labels each tab, marks the current one and announces the Review badge", () => {
    expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: /^Review\s*, 3 to review$/ })).toHaveAttribute("href", "#review");
  });

  it("More opens the other destinations; Escape closes it and returns focus", async () => {
    const more = screen.getByRole("button", { name: "More" });
    await userEvent.click(more);
    expect(more).toHaveAttribute("aria-expanded", "true");
    const menu = document.getElementById(more.getAttribute("aria-controls")!)!;
    expect(menu.querySelector("a[href='#settings']")).toHaveTextContent("Settings");
    expect(menu.querySelector("a[href='#subscriptions']")).toHaveTextContent("Subscriptions");

    await userEvent.keyboard("{Escape}");
    expect(more).toHaveAttribute("aria-expanded", "false");
    expect(more).toHaveFocus();
  });

  it("a press outside closes More", async () => {
    await userEvent.click(screen.getByRole("button", { name: "More" }));
    await userEvent.click(document.body);
    expect(screen.getByRole("button", { name: "More" })).toHaveAttribute("aria-expanded", "false");
  });
});
