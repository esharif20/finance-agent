/** One forecast month: the projected balance in pence and its prediction interval. */
export type Band = { month: string; point: number; lower: number; upper: number };

export type Geometry = {
  y: (minor: number) => number;
  points: { x: number; y: number; yLower: number; yUpper: number }[];
  line: string;
  band: string;
  max: number;
  min: number;
};

/** A round step for the axis ends: 1, 2 or 5 times a power of ten, about a fifth of the span. */
function step(span: number): number {
  const raw = Math.max(1, span / 5);
  const p = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 5, 10].map((k) => k * p).find((s) => s >= raw) ?? 10 * p;
}

/** Pixel geometry for the chart. The scale fits the band with a little room (rounded to a step),
    so the line and band fill the box; it reaches zero only when a month goes below it, so a dip
    under zero stays visible. Presentation only: the figures themselves are untouched. */
export function chartGeometry(months: Band[], w: number, h: number): Geometry {
  const hi = months.length ? Math.max(...months.map((m) => m.upper)) : 0;
  const lo = months.length ? Math.min(...months.map((m) => m.lower)) : 0;
  const pad = (hi - lo) * 0.12 || Math.abs(hi) * 0.1 || 100;
  const st = step(hi - lo + 2 * pad);
  const max = Math.ceil((hi + pad) / st) * st;
  const min = lo < 0 ? Math.floor((lo - pad) / st) * st : Math.max(0, Math.floor((lo - pad) / st) * st);
  const span = max - min || 1;
  const y = (v: number) => h - ((v - min) / span) * h;
  const points = months.map((m, i) => ({
    x: months.length === 1 ? 0 : (i / (months.length - 1)) * w,
    y: y(m.point),
    yLower: y(m.lower),
    yUpper: y(m.upper),
  }));
  const line = points.map((p, i) => `${i ? "L" : "M"}${p.x} ${p.y}`).join("");
  const band = points.length
    ? [...points.map((p, i) => `${i ? "L" : "M"}${p.x} ${p.yUpper}`), ...[...points].reverse().map((p) => `L${p.x} ${p.yLower}`)].join("") + "Z"
    : "";
  return { y, points, line, band, max, min };
}
