import { render } from "@testing-library/react";
import { readFileSync } from "node:fs";
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

const css = readFileSync("src/index.css", "utf8");

describe("odometer, spotlight and reduced motion", () => {
  it("there is no reel: the figure is plain text", () => {
    const { container } = render(<Money value={{ minor: 1234567, currency: "GBP" }} odometer="r" />);
    expect(container.querySelector(".odo-reel")).toBeNull();
    expect(container.querySelector("[aria-hidden=true]")).toHaveTextContent("£12,345.67");
  });

  it("the reel's CSS never animates under reduced motion", () => {
    expect(css).toMatch(/prefers-reduced-motion: reduce\) \{[^]*\.odometer[^{]*\{[^}]*animation:\s*none/);
  });

  it("the roll is under 500ms, on keyframes that fill both ways and end on the real digit", () => {
    expect(css).toMatch(/\.odometer\[data-play\] \.odo-reel \{[^}]*animation:[^;]*3[0-9]{2}ms[^;]*both/);
    expect(css).toMatch(/@keyframes odo-roll \{[^]*to \{[^}]*var\(--d\)/);
  });

  it("the spotlight is for fine hover pointers only and dims without a transition under reduced motion", () => {
    expect(css).toMatch(/@media \(hover: hover\) and \(pointer: fine\) \{[^]*\.spotlight[^{]*:hover[^{]*\{[^}]*opacity:\s*0?\.35/);
    expect(css).toMatch(/prefers-reduced-motion: reduce\) \{[^]*\.spotlight[^{]*\{[^}]*transition:\s*none/);
  });
});
