import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "@/App";

// synthetic
const m = (minor: number) => ({ currency: "GBP", minor });
const line = (name: string, minor: number, reason: string, kind = "category") => ({ name: { data: name }, kind, amount: m(minor), reference: m(minor), reason });
let body: object;

beforeEach(() => {
  window.location.hash = "#plan";
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const json = url === "/api/plan/allocation" ? body : url.includes("review") ? { count: 0, items: [] } : {};
    return new Response(JSON.stringify(json), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});

describe("Plan", () => {
  it("shows the API's figures, reasons and the 50/30/20 baseline, with no buttons", async () => {
    body = {
      status: "ok", month: "2026-11", income: m(300000), infeasible_reason: null, evidence_id: "e1",
      lines: [line("Rent", 120000, "fixed_bill", "bill"), line("Holiday fund", 25000, "goal_floor", "goal")],
      baseline: [line("Needs", 150000, "baseline"), line("Wants", 90000, "baseline")],
    };
    render(<App />);
    const rent = await screen.findByText("Rent");
    expect(rent.closest("li")).toHaveTextContent("£1,200.00");
    expect(rent.closest("li")).toHaveTextContent("A bill due this month");
    expect(screen.getByText("Holiday fund").closest("li")).toHaveTextContent("The least that keeps your goal on track.");
    expect(screen.getByText("Needs").closest("li")).toHaveTextContent("£1,500.00");
    expect(within(screen.getByRole("main")).queryByRole("button")).toBeNull();
  });

  it("renders the infeasible reason", async () => {
    body = { status: "infeasible", month: null, income: null, lines: [], baseline: [], evidence_id: "e2", infeasible_reason: "Your limits add up to more than your income." };
    render(<App />);
    expect(await screen.findByText("No plan fits your limits")).toBeInTheDocument();
    expect(screen.getByText("Your limits add up to more than your income.")).toBeInTheDocument();
  });
});
