// Shared by the snap tests (#93): two synthetic accounts and a stack 360px wide.
import { act } from "@testing-library/react";
import { vi } from "vitest";
import type { components } from "@/api/schema";

type Account = components["schemas"]["AccountOut"];
const m = (minor: number) => ({ minor, currency: "GBP" });
const account = (id: number): Account =>
  ({
    id, bank: `Bank ${id}`, name: "Current", kind: "current", masked_number: `••••000${id}`,
    currency: "GBP", bank_balance: m(100), ledger_balance: m(100), gaps: [], art: null, last_import: null, source: "csv",
  }) as Account;
export const accounts = [account(1), account(2)];
export const WIDTH = 360;

/** Stub ResizeObserver; call the result to give the stack its width. */
export function sized() {
  let resize: ResizeObserverCallback = () => {};
  vi.stubGlobal("ResizeObserver", class {
    constructor(cb: ResizeObserverCallback) { resize = cb; }
    observe() {} disconnect() {} unobserve() {}
  });
  return () => act(() => resize([{ contentRect: { width: WIDTH } } as ResizeObserverEntry], {} as ResizeObserver));
}

export const wait = (ms: number) => act(() => new Promise((r) => setTimeout(r, ms)));

const at = (x: number) => ({ pointerId: 1, pointerType: "touch", clientX: x, clientY: 50, button: 0, isPrimary: true });
/** Press at x = 200, move by dx in four steps, then release unless `release` is false. */
export function drag(el: Element, dx: number, fire: typeof import("@testing-library/react").fireEvent, release = true) {
  fire.pointerDown(el, at(200));
  for (let i = 1; i <= 4; i++) fire.pointerMove(el, at(200 + (dx * i) / 4));
  if (release) fire.pointerUp(el, at(200 + dx));
}
