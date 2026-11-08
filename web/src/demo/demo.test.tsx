import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DemoBanner } from "./DemoBanner";

const reload = vi.fn();

beforeEach(() => {
  Object.defineProperty(window, "location", { configurable: true, value: { ...window.location, reload } });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.resetModules();
  reload.mockReset();
  localStorage.clear();
  document.documentElement.removeAttribute("data-private");
});

describe("DemoBanner", () => {
  it("says the data is synthetic", () => {
    render(<DemoBanner />);
    expect(screen.getByRole("complementary", { name: "Demo" })).toHaveTextContent("Synthetic data. Nothing here is real.");
  });

  it.each(["Reset", "Shuffle"])("%s posts to the demo API and reloads", async (name) => {
    const fetch = vi.fn().mockResolvedValue(new Response('{"persona":1}'));
    vi.stubGlobal("fetch", fetch);
    render(<DemoBanner />);
    await userEvent.click(screen.getByRole("button", { name }));
    expect(fetch).toHaveBeenCalledWith(`/api/demo/${name.toLowerCase()}`, { method: "POST" });
    expect(reload).toHaveBeenCalledOnce();
  });

  it("a refused Reset says so and leaves the page", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 403 })));
    render(<DemoBanner />);
    await userEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Try again");
    expect(reload).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Reset" })).toBeEnabled();
  });
});

describe("privacy mode", () => {
  const mount = async () => {
    const { PrivacyProvider } = await import("@/lib/privacy");
    render(<PrivacyProvider>x</PrivacyProvider>);
  };

  it("starts with values shown in the demo, even after the old private default was saved", async () => {
    vi.stubEnv("VITE_DEMO", "1");
    localStorage.setItem("fa-private", "1");
    await mount();
    expect(document.documentElement).not.toHaveAttribute("data-private");
  });

  it("keeps a demo visitor's own choice", async () => {
    vi.stubEnv("VITE_DEMO", "1");
    localStorage.setItem("fa-private-demo", "1");
    await mount();
    expect(document.documentElement).toHaveAttribute("data-private");
  });

  it("stays off by default in the local app", async () => {
    vi.stubEnv("VITE_DEMO", "");
    await mount();
    expect(document.documentElement).not.toHaveAttribute("data-private");
  });
});
