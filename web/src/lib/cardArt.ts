// Card faces (#58). A bank's real art only ever comes from the user's own local file,
// through /api/card-art/<key>. The repo ships the generic faces: a bank colour and a
// simple pattern, never a logo or a trademarked shape.

export const ASPECT = 85.6 / 53.98; // ISO/IEC 7810 ID-1
export const RADIUS = 0.037; // corner radius / width
// Label band darkness over art, for CSS and WebGL alike: the front one is opaque (it crops the
// art's printed name and scheme mark under the balance); a card behind keeps some art.
export const FRONT_SCRIM = 1;
export const BACK_SCRIM = 0.72;

// Generic face hue per art key; none in the violet band 235-290 (DIRECTIONS.md).
// The public demo (VITE_DEMO=1) ships neither these names nor the art route: its accounts are generic.
const DEMO = import.meta.env.VITE_DEMO === "1";
export const FACE_HUE: Record<string, number> = DEMO ? {} : { lloyds: 158, hsbc: 18, revolut: 345 };

type Box = { x: number; y: number; w: number; h: number };

const isCard = (d: Uint8ClampedArray, i: number) =>
  (d[i + 3] as number) > 24 && !((d[i] as number) > 240 && (d[i + 1] as number) > 240 && (d[i + 2] as number) > 240);

/** The card's bounding box inside a scan's white or transparent margin. */
export function cropBox(d: Uint8ClampedArray, w: number, h: number): Box | null {
  let x0 = w;
  let y0 = h;
  let x1 = -1;
  let y1 = -1;
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++)
      if (isCard(d, (y * w + x) * 4)) {
        if (x < x0) x0 = x;
        if (x > x1) x1 = x;
        if (y < y0) y0 = y;
        if (y > y1) y1 = y;
      }
  return x1 < 0 ? null : { x: x0, y: y0, w: x1 - x0 + 1, h: y1 - y0 + 1 };
}

const cache = new Map<string, Promise<HTMLCanvasElement | null>>();

/** The user's local art for `key`, cropped to the card, or null for the generic face. */
export function loadArt(key: string): Promise<HTMLCanvasElement | null> {
  let p = cache.get(key);
  if (!p) {
    p = fetchArt(key).catch(() => null);
    cache.set(key, p);
  }
  return p;
}

async function fetchArt(key: string): Promise<HTMLCanvasElement | null> {
  if (DEMO) return null;
  const res = await fetch(`/api/card-art/${encodeURIComponent(key)}`);
  if (!res.ok || typeof createImageBitmap !== "function") return null;
  const img = await createImageBitmap(await res.blob());
  const full = document.createElement("canvas");
  full.width = img.width;
  full.height = img.height;
  const g = full.getContext("2d", { willReadFrequently: true });
  if (!g) return null;
  g.drawImage(img, 0, 0);
  const box = cropBox(g.getImageData(0, 0, img.width, img.height).data, img.width, img.height);
  if (!box) return null;
  const out = document.createElement("canvas");
  out.width = box.w;
  out.height = box.h;
  out.getContext("2d")?.drawImage(full, box.x, box.y, box.w, box.h, 0, 0, box.w, box.h);
  return out;
}
