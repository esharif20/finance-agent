import { motion, useReducedMotion } from "motion/react";
import type { components } from "@/api/schema";
import { Money } from "@/components/Money";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { groupSpending } from "@/lib/categories";
import { categoryHref } from "@/lib/route";
import "./categories.css";

type Spending = components["schemas"]["SpendingOut"];

const SIZE = 220;
const STROKE = 26;
const R = (SIZE - STROKE) / 2;
const C = 2 * Math.PI * R;
const SEAM = 3; // px of ground between neighbouring arcs

/** Arcs for the drawn slices, as dash lengths along the circle. */
function arcs(minors: number[]) {
  const total = minors.reduce((a, b) => a + b, 0);
  let start = 0;
  return minors.map((m) => {
    const len = (m / total) * C;
    const arc = { len: Math.max(len - (minors.length > 1 ? SEAM : 0), 0.5), start };
    start += len;
    return arc;
  });
}

export function SpendingDonut({ spending, account }: { spending: Spending; account?: number }) {
  const reduce = useReducedMotion();
  const { slices, rows } = groupSpending(spending.categories);
  const placed = arcs(slices.map((s) => s.minor));
  const summary = rows
    .filter((r) => r.percent > 0)
    .slice(0, 5)
    .map((r) => `${r.name} ${r.percent}%`)
    .join(", ");

  return (
    <Card aria-labelledby="spending-title" className="flex flex-col gap-6">
      <CardHeader className="mb-0">
        <CardTitle id="spending-title">Where it went</CardTitle>
      </CardHeader>

      {slices.length === 0 ? (
        <p className="text-muted-foreground">No spending this month.</p>
      ) : (
        <div className="flex flex-col items-center gap-6 sm:flex-row sm:items-center sm:gap-8">
          <div className="relative shrink-0" style={{ width: SIZE, height: SIZE }}>
            <svg
              viewBox={`0 0 ${SIZE} ${SIZE}`}
              role="img"
              aria-label={`Spending by Category: ${summary}.`}
              className="size-full -rotate-90"
            >
              {slices.map((s, i) => {
                const a = placed[i];
                if (!a) return null;
                const dash = `${a.len} ${C - a.len}`;
                return (
                  <motion.circle
                    key={s.key}
                    cx={SIZE / 2}
                    cy={SIZE / 2}
                    r={R}
                    fill="none"
                    stroke={s.colour}
                    strokeWidth={STROKE}
                    // pointer shortcut; keyboard and screen readers use the legend links
                    style={s.key === "other" ? undefined : { cursor: "pointer" }}
                    onClick={() => {
                      if (s.key !== "other") window.location.hash = categoryHref(s.name, spending.month, account);
                    }}
                    initial={reduce ? false : { strokeDasharray: `0 ${C}`, strokeDashoffset: -a.start }}
                    animate={{ strokeDasharray: dash, strokeDashoffset: -a.start }}
                    transition={reduce ? { duration: 0 } : { duration: 0.22, ease: [0.2, 0.8, 0.2, 1] }}
                  />
                );
              })}
            </svg>
            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
              <span className="text-label text-muted-foreground">Spent</span>
              <Money value={spending.total} className="figure text-figure" />
            </div>
          </div>

          <table className="w-full min-w-0 text-left">
            <caption className="sr-only">Spending by Category, largest first</caption>
            <thead className="sr-only">
              <tr>
                <th scope="col">Category</th>
                <th scope="col">Amount</th>
                <th scope="col">Share</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.name} className="h-11">
                  <th scope="row" className="min-w-0 font-medium">
                    <span className="flex items-center gap-3">
                      <span
                        aria-hidden="true"
                        className="size-3 shrink-0 rounded-full"
                        style={{ background: r.colour }}
                      />
                      <a
                        href={categoryHref(r.name, spending.month, account)}
                        className="truncate rounded-md underline-offset-4 hover:underline"
                      >
                        {r.name}
                      </a>
                    </span>
                  </th>
                  <td className="pl-3 text-right">
                    <Money value={{ minor: r.minor, currency: spending.currency }} />
                  </td>
                  <td className="w-12 pl-3 text-right text-muted-foreground">
                    {r.percent > 0 ? `${r.percent}%` : ""}
                  </td>
                </tr>
              ))}
              {spending.unmatched_refunds.minor !== 0 && (
                <tr className="h-11">
                  <th scope="row" className="min-w-0 font-medium">
                    <span className="flex items-center gap-3">
                      <span aria-hidden="true" className="size-3 shrink-0 rounded-full border-2 border-dashed border-[var(--cat-other)]" />
                      <span className="truncate">Refunds (unmatched)</span>
                    </span>
                  </th>
                  <td className="pl-3 text-right">
                    <Money value={spending.unmatched_refunds} />
                  </td>
                  <td className="w-12 pl-3" />
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-sm text-muted-foreground" data-testid="reconcile">
        Money out <Money value={spending.money_out} className="font-semibold text-foreground" /> ={" "}
        <Money value={spending.total} /> spending after refunds
        {spending.refunds.minor !== 0 && (
          <>
            {" "}+ <Money value={spending.refunds} /> refunds received
          </>
        )}
        {spending.fees.minor !== 0 && (
          <>
            {" "}+ <Money value={spending.fees} /> fees
          </>
        )}
      </p>
    </Card>
  );
}
