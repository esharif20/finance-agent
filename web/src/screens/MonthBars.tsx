import { useQuery } from "@tanstack/react-query";
import { motion, useReducedMotion } from "motion/react";
import { useState } from "react";
import { fetchMonthSeries } from "@/api/client";
import { Money } from "@/components/Money";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { formatMoney } from "@/lib/money";
import { usePrivacy } from "@/lib/privacy";
import { monthLabel } from "./MonthCard";

type Side = "money_in" | "money_out";
const SIDES: { key: Side; label: string }[] = [
  { key: "money_in", label: "Money in" },
  { key: "money_out", label: "Money out" },
];

const short = (month: string) =>
  new Date(`${month}-01T00:00:00Z`).toLocaleDateString("en-GB", { month: "short", timeZone: "UTC" });

/** Last 12 months of money in or out. A bar picks the Overview month; figures come from the API. */
export function MonthBars({ month, onChange }: { month: string; onChange: (month: string) => void }) {
  const [side, setSide] = useState<Side>("money_out");
  const reduce = useReducedMotion();
  const { hidden } = usePrivacy();
  const series = useQuery({ queryKey: ["month-series"], queryFn: fetchMonthSeries });
  if (!series.data || series.data.length < 2) return null; // one month has nothing to compare
  const rows = series.data;
  const max = Math.max(...rows.map((r) => r[side].minor), 1);
  const noun = side === "money_in" ? "money in" : "money out";
  const tallest = rows.reduce((a, r) => (r[side].minor > a.minor ? r[side] : a), rows[0]![side]);

  return (
    <Card aria-labelledby="bars-title">
      <CardHeader className="mb-4 flex-wrap gap-3">
        <CardTitle id="bars-title">Months</CardTitle>
        <div role="group" aria-label="Compare" className="flex gap-1 rounded-full bg-raised p-1">
          {SIDES.map((s) => (
            <button
              key={s.key}
              type="button"
              aria-pressed={side === s.key}
              onClick={() => setSide(s.key)}
              className={`min-h-9 rounded-full px-4 text-sm font-semibold transition-colors ${
                side === s.key ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
      </CardHeader>

      <div role="group" aria-label={`${noun} by month`} className="flex h-44 items-stretch gap-1.5 sm:gap-2.5">
        {rows.map((r) => {
          const selected = r.month === month;
          const label = `${monthLabel(r.month)}, ${noun} ${hidden ? "amount hidden" : formatMoney(r[side])}`;
          return (
            <button
              key={r.month}
              type="button"
              aria-pressed={selected}
              aria-label={label}
              title={label}
              onClick={() => onChange(r.month)}
              className="group flex min-w-0 flex-1 flex-col items-stretch justify-end gap-1.5 rounded-[10px] focus-visible:outline-offset-1"
            >
              <span className="flex min-h-0 flex-1 items-end">
                <motion.span
                  className={`block w-full rounded-t-[6px] transition-colors ${
                    selected ? "bg-accent" : "bg-muted-foreground/40 group-hover:bg-muted-foreground/60"
                  }`}
                  initial={false}
                  animate={{ height: `${Math.max((r[side].minor / max) * 100, 2)}%` }}
                  transition={reduce ? { duration: 0 } : { duration: 0.22, ease: [0.2, 0.8, 0.2, 1] }}
                />
              </span>
              <span
                aria-hidden="true"
                className={`text-center text-[11px] leading-4 sm:text-xs ${selected ? "font-semibold text-foreground" : "text-muted-foreground"}`}
              >
                {short(r.month)}
              </span>
            </button>
          );
        })}
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        Tallest bar <Money value={tallest} />. Tap a month to switch.
      </p>

      <table className="sr-only">
        <caption>Money in and money out by month</caption>
        <thead>
          <tr>
            <th>Month</th>
            <th>Money in</th>
            <th>Money out</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.month}>
              <th scope="row">{monthLabel(r.month)}</th>
              <td>
                <Money value={r.money_in} />
              </td>
              <td>
                <Money value={r.money_out} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
