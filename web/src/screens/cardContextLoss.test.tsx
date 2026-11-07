import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { AccountStack } from "./AccountStack";

// Verifier (#58): after the GPU drops the context, the scene is freed and never drawn again.
const scene = { setSize: vi.fn(), setFaces: vi.fn(), update: vi.fn(), render: vi.fn(), dispose: vi.fn() };
const createCardScene = vi.fn(() => scene);
vi.mock("@/three/cardScene", () => ({ createCardScene: () => createCardScene() }));

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

// #129 (folded in from #68): when the browser gives the context back, the WebGL cards return
// without a reload.
async function mountGl() {
  let resize: ResizeObserverCallback = () => {};
  vi.stubGlobal("WebGLRenderingContext", class {});
  vi.stubGlobal("ResizeObserver", class {
    constructor(cb: ResizeObserverCallback) { resize = cb; }
    observe() {} disconnect() {} unobserve() {}
  });
  const view = render(<AccountStack accounts={[account]} onSelect={() => {}} />);
  act(() => resize([{ contentRect: { width: 360 } } as ResizeObserverEntry], {} as ResizeObserver));
  const group = screen.getByRole("group", { name: "Accounts" });
  await vi.waitFor(() => expect(group).toHaveAttribute("data-gl", "true"));
  const canvas = group.querySelector("canvas")!;
  const fire = (type: string) => act(() => { canvas.dispatchEvent(new Event(type, { cancelable: true })); });
  return { ...view, group, fire };
}

it("rebuilds the scene and draws again when the WebGL context is restored", async () => {
  const { group, fire } = await mountGl();
  fire("webglcontextlost");
  expect(group).toHaveAttribute("data-gl", "false");
  createCardScene.mockClear();
  scene.setFaces.mockClear();
  scene.render.mockClear();

  fire("webglcontextrestored");

  await vi.waitFor(() => expect(group).toHaveAttribute("data-gl", "true"));
  expect(createCardScene).toHaveBeenCalledTimes(1);
  expect(scene.setFaces).toHaveBeenCalled(); // the faces go back on the new scene
  expect(scene.render).toHaveBeenCalled();
});

it("ignores a restore it did not lose, so a stray event never builds a second scene", async () => {
  const { group, fire } = await mountGl();
  createCardScene.mockClear();
  scene.dispose.mockClear();

  fire("webglcontextrestored");

  expect(createCardScene).not.toHaveBeenCalled();
  expect(scene.dispose).not.toHaveBeenCalled();
  expect(group).toHaveAttribute("data-gl", "true");
});

it("asks the browser to restore a lost context", async () => {
  const { group } = await mountGl();
  const lost = new Event("webglcontextlost", { cancelable: true });
  act(() => { group.querySelector("canvas")!.dispatchEvent(lost); });
  expect(lost.defaultPrevented).toBe(true); // without this the browser never fires the restore
});
