import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Money } from "@/components/Money";

// Motion caches the preference on first read, so this lives in its own file.
vi.stubGlobal("matchMedia", (q: string) => ({
  matches: q.includes("prefers-reduced-motion"),
  media: q,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
}));

describe("reduced motion", () => {
  it("shows the final figure at once instead of counting up", () => {
    const { container } = render(<Money value={{ minor: 93690, currency: "GBP" }} countUp />);
    expect(container.querySelector("[aria-hidden=true]")).toHaveTextContent("£936.90");
  });
});
