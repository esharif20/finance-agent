import { useState } from "react";
import { Money } from "@/components/Money";
import { Card } from "@/components/ui/card";
import { type Band, chartGeometry } from "@/lib/forecast";

const W = 560;
const H = 160;
const monthName = (m: string) =>
  new Date(`${m}-01T00:00:00Z`).toLocaleDateString("en-GB", { month: "short", timeZone: "UTC" });

/**
 * The balance forecast: a line for the projection and a band for its prediction interval.
 * Axis values and the tooltip are `Money`, so Hide amounts blurs them; a table carries the same
 * figures for screen readers. Static: it draws once and never animates.
 */
export function ForecastChart({ months, currency = "GBP" }: { months: Band[]; currency?: string }) {
  const [at, setAt] = useState<number>();
  if (!months.length) return null;
  const g = chartGeometry(months, W, H);
  const gbp = (minor: number) => ({ minor, currency });
  const ticks = [g.max, 0, ...(g.min < 0 ? [g.min] : [])];
  const hot = at === undefined ? undefined : months[at];
  const pt = at === undefined ? undefined : g.points[at];
  const pick = (e: React.PointerEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const f = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    setAt(Math.round(f * (months.length - 1)));
  };
  return (
    <Card aria-labelledby="forecast-title">
      <h2 id="forecast-title" className="text-heading">
        Spending forecast
      </h2>
      <p className="text-label text-muted-foreground">Money out each month. The line is the projection; the shaded band is its likely range.</p>
      <div className="mt-4 flex gap-3">
        <div className="relative w-16 shrink-0 text-label text-muted-foreground tabular-nums" style={{ height: H }}>
          {ticks.map((t) => (
            <span key={t} className="absolute right-0 -translate-y-1/2" style={{ top: g.y(t) }}>
              <Money value={gbp(t)} />
            </span>
          ))}
        </div>
        <div
          className="relative min-w-0 flex-1 touch-pan-y"
          onPointerMove={pick}
          onPointerLeave={() => setAt(undefined)}
        >
          <svg viewBox={`-6 -6 ${W + 12} ${H + 12}`} className="block w-full overflow-visible" aria-hidden="true">
            <line x1="0" x2={W} y1={g.y(0)} y2={g.y(0)} stroke="var(--border)" strokeWidth="1" />
            <path d={g.band} fill="var(--data)" fillOpacity="0.2" />
            <path d={g.line} fill="none" stroke="var(--data)" strokeWidth="2" strokeLinejoin="round" />
            {pt && <circle cx={pt.x} cy={pt.y} r="4" fill="var(--data)" stroke="var(--card)" strokeWidth="2" />}
          </svg>
          {hot && pt && (
            <div
              className="pointer-events-none absolute z-10 -translate-x-1/2 rounded-card bg-background px-3 py-2 text-label shadow-[var(--glass-shadow)]"
              style={{ left: `${(pt.x / W) * 100}%`, top: -8, transform: "translate(-50%, -100%)" }}
            >
              <p className="font-semibold">{monthName(hot.month)}</p>
              <p>
                <Money value={gbp(hot.point)} />
              </p>
              <p className="text-muted-foreground">
                <Money value={gbp(hot.lower)} /> to <Money value={gbp(hot.upper)} />
              </p>
            </div>
          )}
          <div className="mt-2 flex justify-between text-label text-muted-foreground" aria-hidden="true">
            {months.map((m) => (
              <span key={m.month}>{monthName(m.month)}</span>
            ))}
          </div>
        </div>
      </div>
      <table className="sr-only">
        <caption>Spending forecast by month</caption>
        <thead>
          <tr>
            <th scope="col">Month</th>
            <th scope="col">Projected</th>
            <th scope="col">Low</th>
            <th scope="col">High</th>
          </tr>
        </thead>
        <tbody>
          {months.map((m) => (
            <tr key={m.month}>
              <th scope="row">{m.month}</th>
              <td>
                <Money value={gbp(m.point)} />
              </td>
              <td>
                <Money value={gbp(m.lower)} />
              </td>
              <td>
                <Money value={gbp(m.upper)} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
