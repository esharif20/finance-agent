import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { AccountStack } from "./AccountStack";

// Verifier (#58): after the GPU drops the context, the scene is freed and never drawn again.
const scene = { setSize: vi.fn(), setFaces: vi.fn(), update: vi.fn(), render: vi.fn(), dispose: vi.fn() };
vi.mock("@/three/cardScene", () => ({ createCardScene: () => scene }));

type Account = components["schemas"]["AccountOut"];
const m = (minor: number) => ({ minor, currency: "GBP" });
const account: Account = {
  id: 1, bank: "Lloyds", name: "Current", kind: "current", masked_number: "••••0001",
  currency: "GBP", bank_balance: m(100), ledger_balance: m(100), gaps: [], art: null, last_import: null, source: "csv",
} as Account;

afterEach(() => vi.unstubAllGlobals());

it("disposes the scene and stops rendering when the WebGL context is lost", async () => {
  let resize: ResizeObserverCallback = () => {};
  vi.stubGlobal("WebGLRenderingContext", class {});
  vi.stubGlobal("ResizeObserver", class {
    constructor(cb: ResizeObserverCallback) { resize = cb; }
    observe() {} disconnect() {} unobserve() {}
  });
  render(<AccountStack accounts={[account]} onSelect={() => {}} />);
  act(() => resize([{ contentRect: { width: 360 } } as ResizeObserverEntry], {} as ResizeObserver));
  const group = screen.getByRole("group", { name: "Accounts" });
  await vi.waitFor(() => expect(group).toHaveAttribute("data-gl", "true"));

  act(() => { group.querySelector("canvas")!.dispatchEvent(new Event("webglcontextlost", { cancelable: true })); });

  expect(group).toHaveAttribute("data-gl", "false");
  expect(scene.dispose).toHaveBeenCalledTimes(1);
  scene.render.mockClear();
  act(() => resize([{ contentRect: { width: 380 } } as ResizeObserverEntry], {} as ResizeObserver));
  await new Promise((r) => setTimeout(r, 50));
  expect(scene.render).not.toHaveBeenCalled();
});

it("disposes the scene, with every card's face and back, when the stack unmounts (#93)", async () => {
  let resize: ResizeObserverCallback = () => {};
  vi.stubGlobal("WebGLRenderingContext", class {});
  vi.stubGlobal("ResizeObserver", class {
    constructor(cb: ResizeObserverCallback) { resize = cb; }
    observe() {} disconnect() {} unobserve() {}
  });
  scene.dispose.mockClear();
  const { unmount } = render(<AccountStack accounts={[account]} onSelect={() => {}} />);
  act(() => resize([{ contentRect: { width: 360 } } as ResizeObserverEntry], {} as ResizeObserver));
  await vi.waitFor(() => expect(screen.getByRole("group", { name: "Accounts" })).toHaveAttribute("data-gl", "true"));
  expect(scene.setFaces).toHaveBeenLastCalledWith(expect.any(Array), expect.any(String)); // the page colour for the backs

  unmount();

  expect(scene.dispose).toHaveBeenCalledTimes(1);
});
