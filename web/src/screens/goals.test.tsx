import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "@/App";

// synthetic
const goal = (over: object) => ({
  id: 1, text: "Holiday fund", amount_minor: 100000, due_on: "2026-06-01", started_on: "2026-03-01",
  linked_account_id: null, status: "at_risk", estimated: true, saved_minor: 20000, remaining_minor: 80000,
  weeks_left: 10, weekly_target_minor: 8000, planned_weekly_minor: 5000, shortfall_minor: 3000, ...over,
});
let body: object;

beforeEach(() => {
  window.location.hash = "#goals";
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const json = url === "/api/goals" ? body : url.includes("review") ? { count: 0, items: [] } : {};
    return new Response(JSON.stringify(json), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});

describe("Goals", () => {
  it("shows the engine's target, an estimate note and the at-risk state, with no payment action", async () => {
    body = { goals: [goal({})], payday: false, checkin: null };
    render(<App />);
    expect(await screen.findByText("Holiday fund")).toBeInTheDocument();
    expect(screen.getByText("At risk")).toBeInTheDocument();
    expect(screen.getByText(/progress is estimated/)).toBeInTheDocument();
    expect(screen.getByText("To save each week").nextElementSibling).toHaveTextContent("£80.00");
    expect(within(screen.getByRole("main")).queryByRole("button")).toBeNull();
  });

  it("shows the payday check-in only when the API sends one", async () => {
    body = { goals: [goal({ status: "on_track", estimated: false, shortfall_minor: null })], payday: true, checkin: { title: "Payday check-in", body: "A minute now." } };
    render(<App />);
    expect(await screen.findByText("Payday check-in")).toBeInTheDocument();
    expect(screen.queryByText(/progress is estimated/)).toBeNull();
  });

  it("invites a first goal when there are none", async () => {
    body = { goals: [], payday: false, checkin: null };
    render(<App />);
    expect(await screen.findByText(/No goals yet/)).toBeInTheDocument();
  });
});
