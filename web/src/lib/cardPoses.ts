// Where each card sits in the account stack (#58). Pure, so it is tested in jsdom and
// shared by the DOM cards and the three.js scene: both read the same numbers.
// Units: px, degrees. y grows downwards (CSS); the scene flips it.

export const PEEK = 64; // the least of a card left showing above the next one, px
const LAYER_Z = 8; // depth between layers; a hover adds less, so order never flips
const TILT = 10; // max degrees towards the pointer
const FAN_DEG = 1.6; // small, so a fanned card's corner never reaches the label of the one behind
const FAN_X = 6;
const REVEAL = 22; // the cards in front of a hovered one move down this far to show more of it
const FLIP_LIFT = 48; // a turning card comes forward so its edge never cuts the card behind

/** Sizes for `count` cards (All accounts first) of height `cardH`: All accounts is a compact
    half card; fanned, each account shows half its face; chosen, the cards behind share what
    the front card leaves. One height for both, so nothing below the stack moves. */
export function stackLayout(count: number, cardH: number) {
  const allH = Math.round(cardH / 2);
  const m = Math.max(1, count - 1);
  const fanPeek = cardH - allH; // All's bottom meets the last account's bottom
  const height = Math.max(m * fanPeek + allH, m * PEEK + cardH);
  return { allH, fanPeek, peek: (height - cardH) / m, height };
}

export type Pose = {
  x: number;
  y: number;
  z: number;
  rx: number;
  ry: number;
  rz: number;
  s: number;
  layer: number;
};

export type Hover = { index: number; px: number; py: number }; // pointer at (px, py) in 0..1

export function stackPoses(
  count: number,
  front: number,
  o: {
    fan: boolean;
    reduce: boolean;
    hover?: Hover | null;
    rising?: number | null;
    /** Drag progress of the front card, -1..1: it turns that far through a half turn (#93). */
    flip?: number;
    peek?: number;
    fanPeek?: number;
  },
): Pose[] {
  const order = [...Array(count).keys()].filter((i) => i !== front);
  order.push(front);
  const back = count - 1;
  const step = o.fan ? (o.fanPeek ?? PEEK) : (o.peek ?? PEEK);
  const poses: Pose[] = Array(count);
  order.forEach((card, layer) => {
    const isFront = layer === back;
    const offset = layer - (back - 1) / 2; // -1, 0, 1 for three back cards
    const fan = o.fan && !isFront && !o.reduce;
    poses[card] = {
      x: fan ? offset * FAN_X : 0,
      y: layer * step,
      z: layer * LAYER_Z,
      rx: 0,
      ry: 0,
      rz: fan ? offset * FAN_DEG : 0,
      s: isFront || o.fan ? 1 : 0.96, // tucked behind a chosen account
      layer,
    };
  });
  if (o.reduce) return poses;

  const h = o.hover;
  const lifted = h ? poses[h.index] : undefined;
  if (h && lifted) {
    const isFront = lifted.layer === back;
    // It lifts a little; the cards in front of it move down instead of it rising over the
    // one behind, so no card ever slides over another's label.
    lifted.y -= isFront ? 6 : 4;
    lifted.z += LAYER_Z / 2;
    lifted.rx = (0.5 - h.py) * 2 * TILT;
    lifted.ry = (h.px - 0.5) * 2 * TILT;
    for (const n of poses) {
      if (n.layer > lifted.layer) n.y += REVEAL;
      if (n.layer === lifted.layer - 1) n.s *= 0.985;
    }
  }
  const f = o.flip ? poses[front] : undefined;
  if (f && o.flip) {
    f.ry += o.flip * 180;
    f.z += Math.abs(Math.sin(o.flip * Math.PI)) * FLIP_LIFT;
  }
  const r = o.rising == null ? undefined : poses[o.rising];
  if (r) {
    r.z += 60;
    r.s *= 1.03;
    r.y -= 10;
  }
  return poses;
}
