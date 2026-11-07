// Where each card sits in the account stack (#58). Pure, so it is tested in jsdom and
// shared by the DOM cards and the three.js scene: both read the same numbers.
// Units: px, degrees. y grows downwards (CSS); the scene flips it.

export const PEEK = 64; // px of each card left showing above the next one
const LAYER_Z = 8; // depth between layers; a hover adds less, so order never flips
const TILT = 7; // max degrees towards the pointer
const FAN_DEG = 2.4;
const FAN_X = 5;

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
  o: { fan: boolean; reduce: boolean; hover?: Hover | null; rising?: number | null },
): Pose[] {
  const order = [...Array(count).keys()].filter((i) => i !== front);
  order.push(front);
  const back = count - 1;
  const poses: Pose[] = Array(count);
  order.forEach((card, layer) => {
    const isFront = layer === back;
    const offset = layer - (back - 1) / 2; // -1, 0, 1 for three back cards
    const fan = o.fan && !isFront && !o.reduce;
    poses[card] = {
      x: fan ? offset * FAN_X : 0,
      y: layer * PEEK,
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
    lifted.y -= isFront ? 6 : 14;
    lifted.z += LAYER_Z / 2;
    lifted.rz += isFront ? 0 : -1.2;
    lifted.rx = (0.5 - h.py) * 2 * TILT;
    lifted.ry = (h.px - 0.5) * 2 * TILT;
    for (const n of poses)
      if (Math.abs(n.layer - lifted.layer) === 1) {
        n.y += 4;
        n.s *= 0.99;
      }
  }
  const r = o.rising == null ? undefined : poses[o.rising];
  if (r) {
    r.z += 60;
    r.s *= 1.03;
    r.y -= 10;
  }
  return poses;
}
