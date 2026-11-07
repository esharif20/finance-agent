import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { PrivacyProvider } from "@/lib/privacy";
import { AccountStack } from "./AccountStack";

// Privacy mode hides the user's own card art as well as amounts: a card image can carry a
// name or a number, so every face falls back to the generic one while amounts are hidden.
const ART = "data:image/png;base64,QUJD";
const fakeArt = { toDataURL: () => ART } as unknown as HTMLCanvasElement;
vi.mock("@/lib/cardArt", async (original) => ({
  ...(await original<typeof import("@/lib/cardArt")>()),
  loadArt: async () => fakeArt,
}));
const scene = { setSize: vi.fn(), setFaces: vi.fn(), update: vi.fn(), render: vi.fn(), dispose: vi.fn() };
vi.mock("@/three/cardScene", () => ({ createCardScene: () => scene }));

type Account = components["schemas"]["AccountOut"];
const m = (minor: number) => ({ minor, currency: "GBP" });
const account = {
  id: 1, bank: "HSBC UK", name: "Current", kind: "current", masked_number: "••••0001",
  currency: "GBP", bank_balance: m(100), ledger_balance: m(100), gaps: [], art: "hsbc", last_import: null, source: "csv",
} as Account;

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.removeItem("fa-private");
  document.documentElement.removeAttribute("data-private");
});

const mount = () =>
  render(
    <PrivacyProvider>
      <AccountStack accounts={[account]} onSelect={() => {}} />
    </PrivacyProvider>,
  );

it("CSS cards drop the user's art while amounts are hidden", async () => {
  mount();
  const card = screen.getByRole("button", { name: /HSBC UK/ });
  await vi.waitFor(() => expect(card).toHaveAttribute("data-art", "true"));
  expect(card.style.backgroundImage).toContain(ART);

  await userEvent.keyboard("h");

  expect(card).toHaveAttribute("data-art", "false");
  expect(card.style.backgroundImage).not.toContain(ART);
});

it("WebGL faces get the generic face while amounts are hidden", async () => {
  let resize: ResizeObserverCallback = () => {};
  vi.stubGlobal("WebGLRenderingContext", class {});
  vi.stubGlobal("ResizeObserver", class {
    constructor(cb: ResizeObserverCallback) { resize = cb; }
    observe() {} disconnect() {} unobserve() {}
  });
  localStorage.setItem("fa-private", "1");
  mount();
  act(() => resize([{ contentRect: { width: 360 } } as ResizeObserverEntry], {} as ResizeObserver));
  const group = screen.getByRole("group", { name: "Accounts" });
  await vi.waitFor(() => expect(group).toHaveAttribute("data-gl", "true"));
  await new Promise((r) => setTimeout(r, 20)); // the art has loaded by now
  const faces = () => scene.setFaces.mock.calls.flatMap(([f]) => f as { art: unknown }[]);
  expect(faces().length).toBeGreaterThan(0);
  expect(faces().every((f) => f.art === null)).toBe(true);

  scene.setFaces.mockClear();
  await userEvent.keyboard("h");
  await vi.waitFor(() => expect(faces().some((f) => f.art === fakeArt)).toBe(true));
});
