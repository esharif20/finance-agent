import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App, queryClient } from "@/App";
import { afterEach as clearAfter } from "vitest";

// App keeps one QueryClient with a staleTime: each test starts from an empty cache
clearAfter(() => queryClient.clear());

// synthetic
const gbp = (minor: number) => ({ minor, currency: "GBP" });
const story = (by: "model" | "template") => ({
  recap: {
    month: "2026-03",
    evidence_id: "story:2026-03",
    wording: null,
    changes: [
      {
        category: "Eating out",
        spent: gbp(30000),
        expected: gbp(20000),
        change: gbp(10000),
        seasonal: false,
        drivers: [{ merchant: { data: "Synth Cafe" }, spent: gbp(9000), expected: gbp(4000), change: gbp(5000) }],
      },
    ],
    anomalies: [],
  },
  text: "Eating out ran high in March.",
  worded_by: by,
  saved: by === "model",
});
let by: "model" | "template";
beforeEach(() => {
  by = "template";
  window.location.hash = "#story/2026-03";
  localStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/api/story/2026-03") return Response.json(story(by));
      if (url === "/api/months") return Response.json(["2026-03", "2026-02"]);
      return Response.json([]);
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

describe("Story", () => {
  it("shows the API's figures with evidence links", async () => {
    render(<App />);
    expect(await screen.findByText("Eating out ran high in March.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Eating out" })).toHaveAttribute("href", "#category/Eating%20out/2026-03");
    expect(screen.getAllByText("+£100.00").length).toBeGreaterThan(0);
    expect(screen.getByText("Synth Cafe")).toBeInTheDocument();
  });
  it("says so when the deterministic fallback wrote the text", async () => {
    render(<App />);
    expect(await screen.findByText(/not worded by the model/)).toBeInTheDocument();
  });
  it("says the model worded it when it did", async () => {
    by = "model";
    render(<App />);
    await waitFor(() => expect(screen.getByText(/Worded by the local model/)).toBeInTheDocument());
  });
  it("switches month through the hash", async () => {
    render(<App />);
    await screen.findByText("Eating out ran high in March.");
    screen.getByRole("button", { name: "Previous month" }).click();
    expect(window.location.hash).toBe("#story/2026-02");
  });
});
