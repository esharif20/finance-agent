import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Money } from "@/components/Money";
import { PrivacyProvider } from "@/lib/privacy";

vi.stubGlobal("matchMedia", (q: string) => ({
  matches: false,
  media: q,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
}));

const v = { minor: 1234567, currency: "GBP" };
const resting = (el: Element) =>
  [...el.querySelectorAll<HTMLElement>(".odo-col")].map((c) => Number(c.style.getPropertyValue("--d"))).join("");

describe("balance odometer", () => {
  it("the final frame rests on the API's exact digits", () => {
    const { container } = render(<Money value={v} odometer="a" />);
    expect(resting(container)).toBe("1234567"); // £12,345.67
    for (const reel of container.querySelectorAll(".odo-reel"))
      expect(reel.textContent).toBe("0123456789"); // no filler digit, in order
  });

  it("carries the real figure in the label and hides the reels", () => {
    const { container } = render(<Money value={v} odometer="b" />);
    expect(container.querySelector(".sr-only")).toHaveTextContent("£12,345.67");
    expect(container.querySelector(".odo-reel")?.closest("[aria-hidden=true]")).not.toBeNull();
    expect(container.querySelector(".odometer")).toHaveAttribute("data-play");
  });

  it("plays once per session: a later mount shows plain text", () => {
    render(<Money value={v} odometer="c" />);
    const { container } = render(<Money value={{ ...v, minor: 5 }} odometer="c" />);
    expect(container.querySelector(".odometer")).toBeNull();
    expect(container.querySelector("[aria-hidden=true]")).toHaveTextContent("£0.05");
  });

  it("a refetch never replays and still rests on the new exact figure", () => {
    const { container, rerender } = render(<Money value={v} odometer="d" />);
    rerender(<Money value={{ ...v, minor: 99 }} odometer="d" />);
    expect(resting(container)).toBe("099");
    render(<Money value={v} odometer="d" />); // remounting later: plain
    expect(document.body.querySelectorAll(".odometer")).toHaveLength(1);
  });

  it("reel geometry (#100): window, line, digit height and offset share one whole-pixel step", () => {
    const css = readFileSync(resolve(__dirname, "../index.css"), "utf8");
    const block = (sel: string) =>
      css.match(new RegExp(`${sel.replace(/[.>\s]/g, (c) => `\\${c}`)}\\s*\\{([^}]*)\\}`))?.[1] ?? "";
    const col = block(".odometer .odo-col");
    expect(col).toMatch(/--odo-h:\s*round\(up,[^)]*1px\)/); // never a fractional step
    expect(col).toMatch(/height:\s*var\(--odo-h\)/);
    expect(col).toMatch(/line-height:\s*var\(--odo-h\)/);
    expect(block(".odometer .odo-reel > span")).toMatch(/height:\s*var\(--odo-h\)/);
    expect(block(".odometer .odo-reel")).toMatch(/var\(--d\) \* var\(--odo-h\)/); // rest
    expect(css).toMatch(/to\s*\{\s*transform:\s*translateY\(calc\(var\(--d\) \* var\(--odo-h\) \* -1\)\)/); // roll end
    expect(css).not.toMatch(/--d\) \* -1\.15em/); // no em-based offset left
  });

  it("shows no reel under Hide amounts", () => {
    localStorage.setItem("fa-private", "1");
    const { container } = render(
      <PrivacyProvider>
        <Money value={v} odometer="e" />
      </PrivacyProvider>,
    );
    localStorage.removeItem("fa-private");
    expect(container.querySelector(".odo-reel")).toBeNull();
    expect(container.querySelector(".sr-only")).toHaveTextContent("Amount hidden");
  });
});
