import { describe, expect, it } from "vitest";
import { cropBox } from "./cardArt";

// An RGBA bitmap: white margin, a transparent corner, and the card itself.
function bitmap(w: number, h: number, card: { x: number; y: number; w: number; h: number }) {
  const d = new Uint8ClampedArray(w * h * 4).fill(255);
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      if (x < 2 && y < 2) d[i + 3] = 0; // transparent
      if (x >= card.x && x < card.x + card.w && y >= card.y && y < card.y + card.h) {
        d[i] = 16;
        d[i + 1] = 180;
        d[i + 2] = 120;
      }
    }
  return d;
}

describe("cropBox", () => {
  it("finds the card inside a white or transparent margin", () => {
    expect(cropBox(bitmap(40, 30, { x: 5, y: 4, w: 30, h: 19 }), 40, 30)).toEqual({
      x: 5,
      y: 4,
      w: 30,
      h: 19,
    });
  });

  it("keeps a full-bleed image whole", () => {
    expect(cropBox(bitmap(20, 12, { x: 0, y: 0, w: 20, h: 12 }), 20, 12)).toEqual({
      x: 0,
      y: 0,
      w: 20,
      h: 12,
    });
  });

  it("gives up on an image with nothing in it", () => {
    expect(cropBox(new Uint8ClampedArray(10 * 10 * 4).fill(255), 10, 10)).toBeNull();
  });
});
