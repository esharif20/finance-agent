import { render } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { NavTabs } from "@/components/NavTabs";

// Motion caches the preference on first read, so this lives in its own file.
vi.stubGlobal("matchMedia", (q: string) => ({
  matches: q.includes("prefers-reduced-motion"),
  media: q,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
}));

const css = readFileSync("src/index.css", "utf8");
const reduced = css.slice(css.lastIndexOf("/* motion polish"));

describe("reduced motion", () => {
  it("the marker jumps instead of sliding", () => {
    const { container } = render(<NavTabs tabs={[{ id: "a", label: "A" }]} tab="a" className="" item="" />);
    expect(container.querySelector("[data-marker]")).toHaveAttribute("data-reduced", "true");
  });

  it("the icons crossfade only: no rotate or scale", () => {
    expect(reduced).toMatch(/prefers-reduced-motion: reduce\) \{[^]*\.icon-swap[^{]*\{[^}]*transform:\s*none/);
  });

  it("the count tick drops its movement", () => {
    expect(reduced).toMatch(/prefers-reduced-motion: reduce\) \{[^]*\.count-tick[^{]*\{[^}]*transform:\s*none/);
  });
});
