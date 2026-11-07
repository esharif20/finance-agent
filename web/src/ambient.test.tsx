import { act, render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Ambient } from "@/components/Ambient";

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("ambient background", () => {
  it("is decoration only: hidden from assistive tech and never takes input", () => {
    const { container } = render(<Ambient />);
    const root = container.firstElementChild as HTMLElement;
    expect(root).toHaveAttribute("aria-hidden", "true");
    expect(root).toHaveClass("ambient");
  });

  it("pauses while the tab is hidden and resumes when it is shown", () => {
    const { container } = render(<Ambient />);
    const root = container.firstElementChild as HTMLElement;
    expect(root).not.toHaveAttribute("data-paused");
    act(() => setHidden(true));
    expect(root).toHaveAttribute("data-paused");
    act(() => setHidden(false));
    expect(root).not.toHaveAttribute("data-paused");
  });
});
