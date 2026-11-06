import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { NoData, type CategoryDetail, fetchCategory, fetchMonths } from "@/api/client";
import { Money } from "@/components/Money";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { categoryColour } from "@/lib/categories";
import { categoryHref } from "@/lib/route";
import { MonthSwitcher } from "./MonthSwitcher";
import { monthLabel } from "./MonthCard";

type Point = { minor: number; currency: string };

/** Bar length as a whole percent of the largest bar; spending only (never negative). */
const width = (minor: number, max: number) => (max > 0 ? Math.max(0, Math.round((minor * 100) / max)) : 0);

function Bars({
  rows,
  currency,
  colour,
}: {
  rows: { name: string; minor: number }[];
  currency: string;
  colour: string;
}) {
  const max = Math.max(0, ...rows.map((r) => r.minor));
  return (
    <ul className="flex flex-col gap-3">
      {rows.map((r) => (
        <li key={r.name}>
          <div className="flex items-baseline justify-between gap-3">
            <span className="min-w-0 truncate font-medium">{r.name}</span>
            <Money value={{ minor: r.minor, currency }} />
          </div>
          <div className="mt-1.5 h-2 rounded-full bg-raised" aria-hidden="true">
            <div className="h-full rounded-full" style={{ width: `${width(r.minor, max)}%`, background: colour }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function Trend({ data, colour }: { data: CategoryDetail; colour: string }) {
  const max = Math.max(0, ...data.trend.map((p) => p.amount.minor));
  const label = (m: string) => monthLabel(m).split(" ")[0]?.slice(0, 3);
  return (
    <ol className="flex h-40 items-end gap-2 sm:gap-3" aria-label="Monthly spending, oldest first">
      {data.trend.map((p) => {
        const current = p.month === data.month;
        return (
          <li key={p.month} className="flex h-full min-w-0 flex-1 flex-col justify-end gap-2">
            <Money
              value={p.amount}
              className={`text-center text-xs ${current ? "font-semibold" : "text-muted-foreground"}`}
            />
            <div className="flex flex-1 items-end">
              <div
                className="w-full rounded-t-lg"
                style={{
                  height: `${Math.max(width(p.amount.minor, max), p.amount.minor > 0 ? 4 : 1)}%`,
                  background: colour,
                  opacity: current ? 1 : 0.4,
                }}
              />
            </div>
            <span className="text-center text-xs text-muted-foreground">{label(p.month)}</span>
          </li>
        );
      })}
    </ol>
  );
}

function Transactions({ data }: { data: CategoryDetail }) {
  return (
    <Card aria-labelledby="txns-title">
      <CardHeader>
        <CardTitle id="txns-title">Transactions</CardTitle>
        <span className="text-sm text-muted-foreground">{data.transactions.length}</span>
      </CardHeader>
      <ul className="divide-y divide-border">
        {data.transactions.map((t) => (
          <li key={t.id} className="flex min-h-14 items-center gap-3 py-2">
            <div className="min-w-0 flex-1">
              <p className="truncate font-medium">{t.merchant ?? t.description}</p>
              <p className="truncate text-sm text-muted-foreground">
                {new Date(`${t.posted_on}T00:00:00Z`).toLocaleDateString("en-GB", {
                  day: "numeric",
                  month: "short",
                  timeZone: "UTC",
                })}
                {t.subcategory ? ` · ${t.subcategory}` : ""}
              </p>
            </div>
            <Money value={t.amount as Point} sign />
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function CategoryScreen({
  category,
  month: chosen,
}: {
  category: string;
  month?: string;
}) {
  const reduce = useReducedMotion();
  const months = useQuery({ queryKey: ["months"], queryFn: fetchMonths });
  const month = chosen ?? months.data?.[0];
  const detail = useQuery({
    queryKey: ["category", category, month],
    queryFn: () => fetchCategory(category, month as string),
    enabled: !!month,
    placeholderData: keepPreviousData,
    retry: (n, e) => !(e instanceof NoData) && n < 2,
  });
  // a Category without its own hue (Uncategorised) falls back to the grey
  const colour = categoryColour(category).replace(")", ", var(--cat-other))");
  const go = (m: string) => {
    window.location.hash = categoryHref(category, m);
  };

  const back = (
    <a
      href="#overview"
      className="inline-flex min-h-11 items-center gap-2 rounded-full bg-raised px-4 text-sm font-semibold transition-colors hover:bg-active"
    >
      <ArrowLeft className="size-4" aria-hidden="true" />
      Overview
    </a>
  );

  const body = () => {
    if (detail.error instanceof NoData) {
      return (
        <Card>
          <h2 className="text-[18px] font-semibold">Nothing in {category} this month</h2>
          <p className="mt-2 text-muted-foreground">Pick another month, or go back to the Overview.</p>
        </Card>
      );
    }
    if (detail.error || months.error) {
      return (
        <Card role="alert">
          <h2 className="text-[18px] font-semibold">Can't reach the local server</h2>
          <p className="mt-2 text-muted-foreground">Run `fa` in a terminal to start it, then try again.</p>
          <button
            type="button"
            onClick={() => detail.refetch()}
            className="mt-4 min-h-11 rounded-full bg-primary px-5 text-sm font-semibold text-primary-foreground"
          >
            Try again
          </button>
        </Card>
      );
    }
    const d = detail.data;
    if (!d) return <Skeleton className="h-[440px] rounded-card" aria-busy="true" />;
    return (
      <div className="flex flex-col gap-6">
        <Card aria-labelledby="cat-total" className="flex flex-col gap-1">
          <span id="cat-total" className="text-sm text-muted-foreground">
            Spent on {d.name}, {monthLabel(d.month)}
          </span>
          <Money value={d.total} className="text-[40px] leading-[48px] font-semibold tracking-[-0.02em]" />
        </Card>
        <div className="grid items-start gap-6 lg:grid-cols-2">
          <Card aria-labelledby="subs-title">
            <CardHeader>
              <CardTitle id="subs-title">Subcategories</CardTitle>
            </CardHeader>
            <Bars
              rows={d.subcategories.map((s) => ({ name: s.name, minor: s.amount.minor }))}
              currency={d.currency}
              colour={colour}
            />
          </Card>
          <Card aria-labelledby="merchants-title">
            <CardHeader>
              <CardTitle id="merchants-title">Top Merchants</CardTitle>
            </CardHeader>
            {d.merchants.length === 0 ? (
              <p className="text-muted-foreground">No Merchants this month.</p>
            ) : (
              <Bars
                rows={d.merchants.map((s) => ({ name: s.name, minor: s.amount.minor }))}
                currency={d.currency}
                colour={colour}
              />
            )}
          </Card>
        </div>
        <Card aria-labelledby="trend-title">
          <CardHeader>
            <CardTitle id="trend-title">Last 6 months</CardTitle>
          </CardHeader>
          <Trend data={d} colour={colour} />
        </Card>
        <Transactions data={d} />
      </div>
    );
  };

  return (
    <motion.div
      className="flex min-h-dvh flex-col gap-6"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={reduce ? { duration: 0 } : { duration: 0.2, ease: [0.2, 0.8, 0.2, 1] }}
    >
      <div className="flex flex-wrap items-center gap-3">
        {back}
        {months.data && month && <MonthSwitcher months={months.data} month={month} onChange={go} />}
      </div>
      <h1 className="flex items-center gap-3 text-[28px] leading-9 font-semibold tracking-[-0.01em]">
        <span aria-hidden="true" className="size-4 rounded-full" style={{ background: colour }} />
        {category}
      </h1>
      {body()}
    </motion.div>
  );
}
