import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AccountStack } from "./AccountStack";
import { accounts, drag, sized, wait, WIDTH } from "./cardSnap.fixture";

// Verifier (#93): adversarial cases for the snap and the scene's lifetime.
const made: { dispose: ReturnType<typeof vi.fn> }[] = [];
vi.mock("@/three/cardScene", () => ({
  createCardScene: () => {
    const s = { setSize: vi.fn(), setFaces: vi.fn(), update: vi.fn(), render: vi.fn(), dispose: vi.fn() };
    made.push(s);
    return s;
  },
}));
afterEach(() => vi.unstubAllGlobals());

it("a keyboard choice after a drag still selects, even when the drag's click went to the stack", async () => {
  const size = sized();
  const onSelect = vi.fn();
  render(<AccountStack accounts={accounts} selected={1} onSelect={onSelect} />);
  size();
  // Captured to the stack, the browser sends the drag's click to the stack, not the card.
  drag(screen.getByRole("button", { name: /Bank 1/ }), 0.2 * WIDTH, fireEvent);
  await wait(250);
  expect(onSelect).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /Bank 2/ }), { detail: 0 }); // Enter or Space
  expect(onSelect).toHaveBeenCalledWith(2);
});

it("over WebGL, a card's labels hide while it is turned, so they never float off the 3D card", async () => {
  vi.stubGlobal("WebGLRenderingContext", class {});
  const size = sized();
  render(<AccountStack accounts={accounts} selected={1} onSelect={() => {}} />);
  size();
  const group = screen.getByRole("group", { name: "Accounts" });
  await vi.waitFor(() => expect(group).toHaveAttribute("data-gl", "true"));
  const front = screen.getByRole("button", { name: /Bank 1/ });
  expect(front).toHaveAttribute("data-turned", "false");
  drag(front, -0.25 * WIDTH, fireEvent, false); // a quarter of the way: well short of edge-on
  await vi.waitFor(() => expect(front).toHaveAttribute("data-turned", "true"));
  fireEvent.pointerUp(front, { pointerId: 1, clientX: 110, clientY: 50 });
  await vi.waitFor(() => expect(front).toHaveAttribute("data-turned", "false"), { timeout: 3000 });
});

it("rapid mount and unmount frees every scene it made", async () => {
  vi.stubGlobal("WebGLRenderingContext", class {});
  made.length = 0;
  for (let i = 0; i < 5; i++) {
    const size = sized();
    const { unmount } = render(<AccountStack accounts={accounts} onSelect={() => {}} />);
    size();
    if (i % 2) await act(() => new Promise((r) => setTimeout(r, 0))); // sometimes after the scene loads
    unmount();
  }
  await wait(50);
  expect(made.length).toBeGreaterThan(0);
  expect(made.filter((s) => s.dispose.mock.calls.length !== 1)).toEqual([]);
});
