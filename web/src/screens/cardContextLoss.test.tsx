import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
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

// Verifier (#129): while the context is lost the CSS cards draw, and a CSS card turned over
// must not show its art mirrored either: the inline art loses to a generic back.
it("gives a turned CSS card a generic back, never its art", () => {
  const css = readFileSync(resolve(__dirname, "../index.css"), "utf8");
  const rule = /\.card-stack:not\(\[data-gl="true"\]\) \.account-card\[data-back="true"\]\s*\{([^}]*)\}/.exec(css);
  expect(rule?.[1]).toMatch(/background-image:[^;]*oklch[^;]*!important/); // beats the inline art
  expect(rule?.[1]).not.toMatch(/url\(/);
});

// Verifier (#129): repeated loss and restore never piles up scenes or canvases, and an
// unmount during a lost context leaves nothing behind for a late restore to revive.
function trackScenes() {
  const made: { dispose: ReturnType<typeof vi.fn>; render: ReturnType<typeof vi.fn> }[] = [];
  createCardScene.mockImplementation(() => {
    const s = { ...scene, dispose: vi.fn(), render: vi.fn() };
    made.push(s);
    return s;
  });
  const live = () => made.filter((s) => s.dispose.mock.calls.length === 0);
  return { made, live };
}
afterEach(() => createCardScene.mockImplementation(() => scene));

it("survives five loss and restore cycles with one canvas and one live scene", async () => {
  const { made, live } = trackScenes();
  const { group, fire, container } = await mountGl();
  for (let i = 0; i < 5; i++) {
    fire("webglcontextlost");
    expect(group).toHaveAttribute("data-gl", "false");
    expect(live()).toHaveLength(0);
    fire("webglcontextrestored");
    await vi.waitFor(() => expect(group).toHaveAttribute("data-gl", "true"));
    expect(live()).toHaveLength(1);
  }
  expect(made).toHaveLength(6);
  for (const s of made) expect(s.dispose.mock.calls.length).toBeLessThanOrEqual(1);
  expect(container.querySelectorAll("canvas")).toHaveLength(1);
});

it("frees everything when unmounted while the context is lost, and a late restore builds nothing", async () => {
  const { made, live } = trackScenes();
  const { group, fire, unmount } = await mountGl();
  const canvas = group.querySelector("canvas")!;
  fire("webglcontextlost");
  unmount();
  act(() => { canvas.dispatchEvent(new Event("webglcontextrestored")); });
  await new Promise((r) => setTimeout(r, 30));
  expect(made).toHaveLength(1);
  expect(live()).toHaveLength(0);
  expect(made[0]!.dispose).toHaveBeenCalledTimes(1);
});
