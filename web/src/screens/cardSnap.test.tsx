import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AccountStack } from "./AccountStack";
import { accounts, drag, sized, wait, WIDTH } from "./cardSnap.fixture";

// #93: dragging or side-scrolling the stack turns the front card; about 170ms after the
// input stops, the stack settles on the nearest card.
afterEach(() => vi.unstubAllGlobals());

function mount(selected?: number) {
  const size = sized();
  const onSelect = vi.fn();
  render(<AccountStack accounts={accounts} selected={selected} onSelect={onSelect} />);
  size();
  return { onSelect, card: (name: RegExp) => screen.getByRole("button", { name }) };
}

describe("card stack snap", () => {
  it("settles on the next card about 170ms after a drag past halfway stops", async () => {
    const { onSelect, card } = mount(1);
    drag(card(/Bank 1/), -0.6 * WIDTH, fireEvent);
    await wait(100);
    expect(onSelect).not.toHaveBeenCalled(); // not while the input may still continue
    await wait(150);
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith(2);
  });

  it("goes back to the same card after a short drag, and the drag is not a click", async () => {
    const { onSelect, card } = mount(1);
    const front = card(/Bank 1/);
    drag(front, 0.2 * WIDTH, fireEvent);
    fireEvent.click(front, { detail: 1 }); // a pointer's click; Enter or Space give detail 0
    await wait(250);
    expect(onSelect).not.toHaveBeenCalled();
  });

  // The dropped click: mid-reshuffle the cards move under a still pointer, so the press goes
  // down on one card and up on another, and the browser's click lands on the stack itself.
  it("chooses the card a press went down on, even when it comes up over another", () => {
    const { onSelect, card } = mount(1);
    const pressed = card(/Bank 2/);
    const under = card(/Bank 1/);
    fireEvent.pointerDown(pressed, { pointerId: 1, button: 0, clientX: 100, clientY: 20 });
    fireEvent.pointerUp(under, { pointerId: 1, button: 0, clientX: 100, clientY: 20 });
    fireEvent.click(screen.getByRole("group", { name: "Accounts" }), { detail: 1 });
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith(2);
  });

  it("counts a press and its click as one choice, and still takes Enter after it", async () => {
    const { onSelect, card } = mount(1);
    const two = card(/Bank 2/);
    fireEvent.pointerDown(two, { pointerId: 1, button: 0, clientX: 100, clientY: 20 });
    fireEvent.pointerUp(two, { pointerId: 1, button: 0, clientX: 100, clientY: 20 });
    fireEvent.click(two, { detail: 1 });
    expect(onSelect).toHaveBeenCalledTimes(1);
    await wait(10);
    fireEvent.click(two, { detail: 0 }); // Enter or Space
    expect(onSelect).toHaveBeenCalledTimes(2);
  });

  it("does not snap while the card is still held", async () => {
    const { onSelect, card } = mount(1);
    drag(card(/Bank 1/), -0.6 * WIDTH, fireEvent, false);
    await wait(250);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("wraps from the last card to All accounts", async () => {
    const { onSelect, card } = mount(2);
    drag(card(/Bank 2/), -0.7 * WIDTH, fireEvent);
    await wait(250);
    expect(onSelect).toHaveBeenCalledWith(undefined);
  });

  it("snaps after a sideways scroll too, and leaves vertical scrolling to the page", async () => {
    const { onSelect } = mount(1);
    const stack = screen.getByRole("group", { name: "Accounts" });
    const side = new WheelEvent("wheel", { deltaX: 0.6 * WIDTH, deltaY: 0, bubbles: true, cancelable: true });
    stack.dispatchEvent(side);
    expect(side.defaultPrevented).toBe(true);
    const down = new WheelEvent("wheel", { deltaX: 0, deltaY: 120, bubbles: true, cancelable: true });
    stack.dispatchEvent(down);
    expect(down.defaultPrevented).toBe(false);
    await wait(250);
    expect(onSelect).toHaveBeenCalledWith(2);
  });
});
