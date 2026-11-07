import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Money } from "@/components/Money";

vi.stubGlobal("matchMedia", (q: string) => ({
  matches: false,
  media: q,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
}));

describe("count-up", () => {
  it("starts from zero when motion is allowed, but screen readers get the final figure", () => {
    const { container } = render(<Money value={{ minor: 93690, currency: "GBP" }} countUp />);
    expect(container.querySelector("[aria-hidden=true]")).toHaveTextContent("£0.00");
    expect(container.querySelector(".sr-only")).toHaveTextContent("£936.90");
  });

  it("does not animate unless asked", () => {
    const { container } = render(<Money value={{ minor: 93690, currency: "GBP" }} />);
    expect(container.querySelector("[aria-hidden=true]")).toHaveTextContent("£936.90");
  });
});
