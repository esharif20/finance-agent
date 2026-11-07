import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AccountStack } from "./AccountStack";
import { accounts, drag, sized, wait, WIDTH } from "./cardSnap.fixture";

// Motion caches the preference on first read, so this lives in its own file.
vi.stubGlobal("matchMedia", (q: string) => ({
  matches: q.includes("prefers-reduced-motion"),
  media: q,
  addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
}));

describe("card stack snap under reduced motion (#93)", () => {
  it("never turns a card while dragging, then jumps to the nearest card", async () => {
    const size = sized();
    const onSelect = vi.fn();
    render(<AccountStack accounts={accounts} selected={1} onSelect={onSelect} />);
    size();
    const front = screen.getByRole("button", { name: /Bank 1/ });
    drag(front, -0.6 * WIDTH, fireEvent, false);
    expect(front.style.transform).toContain("rotateY(0deg)");
    fireEvent.pointerUp(front, { pointerId: 1, clientX: 200 - 0.6 * WIDTH, clientY: 50 });
    await wait(250);
    expect(onSelect).toHaveBeenCalledWith(2);
  });
});
