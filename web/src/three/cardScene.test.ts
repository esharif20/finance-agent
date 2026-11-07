import { afterEach, expect, it, vi } from "vitest";
import { cardBack } from "./cardScene";

// #129: a turned card never shows the bank's art, mirrored or not. The back is drawn from the
// generic face's colour alone, so card art (front-only) cannot reach it.
afterEach(() => vi.restoreAllMocks());

function recordCanvas() {
  const drawn: unknown[] = [];
  const g = new Proxy({} as Record<string, unknown>, {
    get: (t, k) =>
      k === "drawImage"
        ? (src: unknown) => drawn.push(src)
        : k in t
          ? t[k as string]
          : k === "createLinearGradient"
            ? () => ({ addColorStop() {} })
            : () => {},
    set: (t, k, v) => ((t[k as string] = v), true),
  });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(g as never);
  return drawn;
}

it("draws the back without the face's art, even when the card has art", () => {
  const drawn = recordCanvas();
  const art = document.createElement("canvas");
  const back = cardBack({ art, hue: 158, h: 227 }, "#f3f5f7", 512, 323);
  expect(back).toBeInstanceOf(HTMLCanvasElement);
  expect(back).not.toBe(art);
  expect(drawn).not.toContain(art);
  expect([back.width, back.height]).toEqual([512, 323]);
});
