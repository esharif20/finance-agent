import { describe, expect, it } from "vitest";
import { PEEK, stackLayout, stackPoses } from "./cardPoses";

// Four cards: All accounts (0) then three accounts.
describe("stackPoses", () => {
  it("puts the chosen card at the front of the pile, the rest in order behind it", () => {
    const p = stackPoses(4, 2, { fan: false, reduce: false });
    expect(p[2]?.layer).toBe(3);
    expect(p[2]?.y).toBe(3 * PEEK);
    expect([p[0]?.layer, p[1]?.layer, p[3]?.layer]).toEqual([0, 1, 2]);
    expect(Math.max(...p.map((c) => c.z))).toBe(p[2]?.z);
  });

  it("tucks the other cards behind a chosen account", () => {
    const p = stackPoses(4, 2, { fan: false, reduce: false });
    for (const i of [0, 1, 3]) {
      expect(p[i]?.s).toBeLessThan(1);
      expect(p[i]?.rz).toBe(0);
    }
    expect(p[2]?.s).toBe(1);
  });

  it("fans every account card out behind All accounts", () => {
    const p = stackPoses(4, 0, { fan: true, reduce: false });
    const back = [p[1], p[2], p[3]].map((c) => c?.rz ?? 0);
    expect(back[0]).toBeLessThan(0);
    expect(back[1]).toBe(0);
    expect(back[2]).toBeGreaterThan(0);
    expect(back[0]).toBe(-(back[2] as number)); // symmetric fan
    expect(p[0]).toMatchObject({ rz: 0, s: 1, layer: 3 });
  });

  it("lifts the hovered card out of the stack and tilts it towards the pointer", () => {
    const rest = stackPoses(4, 0, { fan: true, reduce: false });
    const p = stackPoses(4, 0, { fan: true, reduce: false, hover: { index: 2, px: 1, py: 0 } });
    expect(p[2]?.y).toBeLessThan(rest[2]?.y as number);
    expect(p[2]?.rx).toBeGreaterThan(0); // the top edge, under the pointer, dips away
    expect(p[2]?.ry).toBeGreaterThan(0); // and so does the right edge
    expect(p[2]?.layer).toBe(rest[2]?.layer); // never jumps over its neighbour
    // the card in front moves down to show more of it; the one behind eases back
    expect(p[3]?.y).toBeGreaterThan(rest[3]?.y as number);
    expect(p[1]?.s).toBeLessThan(rest[1]?.s as number);
    expect(p[1]?.y).toBe(rest[1]?.y);
    expect(p[1]?.rx).toBe(0);
  });

  it("treats keyboard focus like a hover at the card's centre: lift, no tilt", () => {
    const rest = stackPoses(4, 0, { fan: true, reduce: false });
    const p = stackPoses(4, 0, { fan: true, reduce: false, hover: { index: 1, px: 0.5, py: 0.5 } });
    expect(p[1]?.y).toBeLessThan(rest[1]?.y as number);
    expect(p[1]?.rx).toBe(0);
    expect(p[1]?.ry).toBe(0);
  });

  it("has no tilt or travel under reduced motion", () => {
    const rest = stackPoses(4, 2, { fan: false, reduce: true });
    const hovered = stackPoses(4, 2, { fan: false, reduce: true, hover: { index: 1, px: 1, py: 0 } });
    expect(hovered).toEqual(rest);
    for (const c of rest) expect([c.rx, c.ry, c.rz, c.x]).toEqual([0, 0, 0, 0]);
  });

  it("raises a card that is being chosen towards the viewer", () => {
    const settle = stackPoses(4, 2, { fan: false, reduce: false });
    const rising = stackPoses(4, 2, { fan: false, reduce: false, rising: 2 });
    expect(rising[2]?.z).toBeGreaterThan(settle[2]?.z as number);
    expect(rising[2]?.s).toBeGreaterThan(1);
  });

  it("spaces the cards by the peek of the mode: wider when the accounts fan out", () => {
    const fan = stackPoses(4, 0, { fan: true, reduce: false, peek: 100, fanPeek: 160 });
    expect([fan[1]?.y, fan[2]?.y, fan[3]?.y, fan[0]?.y]).toEqual([0, 160, 320, 480]);
    const chosen = stackPoses(4, 2, { fan: false, reduce: false, peek: 100, fanPeek: 160 });
    expect(chosen[2]?.y).toBe(300);
  });
});

describe("stackLayout", () => {
  it("keeps All accounts compact and the stack one height in both modes", () => {
    const l = stackLayout(4, 328);
    expect(l.allH).toBe(164);
    // fanned: three account cards at half a card each, All in front covering the last one's lower half
    expect(l.fanPeek).toBe(164);
    expect(l.height).toBe(3 * 164 + 164);
    // chosen: the same height, the three cards behind share what the front card leaves
    expect(l.peek).toBeCloseTo((l.height - 328) / 3);
    expect(l.peek).toBeLessThan(l.allH); // All accounts never shows below the next card
  });

  it("never peeks less than PEEK, so a card behind stays clickable", () => {
    const l = stackLayout(2, 328);
    expect(l.peek).toBeGreaterThanOrEqual(PEEK);
    expect(l.height).toBe(328 + l.peek);
  });
});
