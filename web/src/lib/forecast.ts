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

/** Pixel geometry for the chart. The scale always includes zero so a dip below it is visible. */
export function chartGeometry(months: Band[], w: number, h: number): Geometry {
  const max = Math.max(0, ...months.map((m) => m.upper));
  const min = Math.min(0, ...months.map((m) => m.lower));
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
