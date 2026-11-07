import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CountTick } from "@/components/CountTick";
import { IconSwap } from "@/components/IconSwap";
import { NavTabs } from "@/components/NavTabs";

const tabs = [
  { id: "overview", label: "Overview" },
  { id: "transactions", label: "Transactions" },
];

describe("NavTabs", () => {
  it("keeps one marker, inside the active tab, and moves it", () => {
    const { container, rerender } = render(<NavTabs tabs={tabs} tab="overview" className="" item="" />);
    expect(container.querySelectorAll("[data-marker]")).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Overview" }).querySelector("[data-marker]")).not.toBeNull();
    rerender(<NavTabs tabs={tabs} tab="transactions" className="" item="" />);
    expect(container.querySelectorAll("[data-marker]")).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Transactions" }).querySelector("[data-marker]")).not.toBeNull();
  });
});

describe("IconSwap", () => {
  it("stacks both icons and shows the one matching the state", () => {
    const { container, rerender } = render(<IconSwap on={false} a={<i />} b={<i />} />);
    const state = () => [...container.querySelectorAll("[data-icon]")].map((e) => e.getAttribute("data-icon"));
    expect(state()).toEqual(["in", "out"]);
    rerender(<IconSwap on a={<i />} b={<i />} />);
    expect(state()).toEqual(["out", "in"]);
  });
});

describe("CountTick", () => {
  it("shows the real figure, and ticks only when it changes", () => {
    const { container, rerender } = render(<CountTick value={12} />);
    expect(container).toHaveTextContent("12");
    expect(container.querySelector("[data-tick]")).toBeNull();
    rerender(<CountTick value={7} />);
    expect(container).toHaveTextContent("7");
    expect(container.querySelector("[data-tick]")).not.toBeNull();
  });
});
