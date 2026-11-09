import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ThumbsDown, ThumbsUp, X } from "lucide-react";
import { useState } from "react";
import { type Insight, dismissInsight, fetchInsights, rateInsight } from "@/api/client";
import { Money } from "@/components/Money";
import { WhatIfDialog } from "@/components/WhatIfDialog";
import { Card } from "@/components/ui/card";
import { confidenceWord, typeTitle } from "@/lib/insights";
import { cn } from "@/lib/utils";

export const INSIGHTS_KEY = ["insights"] as const;

const round =
  "inline-flex size-11 items-center justify-center rounded-full transition-[background-color,transform] duration-150 ease-out active:scale-[0.97] [@media(hover:hover)]:hover:bg-active disabled:opacity-50";

/** The explanation is written by code and quotes pounds; every one is blurred under Hide amounts. */
export function Explained({ text }: { text: string }) {
  return (
    <>
      {text.split(/(£[\d,]+(?:\.\d{2})?)/).map((part, i) =>
        i % 2 ? (
          <span key={i} className="money whitespace-nowrap">
            {part}
          </span>
        ) : (
          part
        ),
      )}
    </>
  );
}

function Row({ insight }: { insight: Insight }) {
  const reduce = useReducedMotion();
  const client = useQueryClient();
  const done = { onSettled: () => void client.invalidateQueries({ queryKey: INSIGHTS_KEY }) };
  const rate = useMutation({ mutationFn: (liked: boolean) => rateInsight(insight.id, liked), ...done });
  const hide = useMutation({ mutationFn: () => dismissInsight(insight.id), ...done });
  const busy = rate.isPending || hide.isPending;
  const mark = rate.isPending ? (rate.variables ? "up" : "down") : insight.signal;
  const n = insight.evidence.length;
  return (
    <motion.li
      layout={!reduce}
      exit={reduce ? { opacity: 0 } : { opacity: 0, x: 16, scale: 0.98 }}
      transition={{ duration: 0.18, ease: [0.23, 1, 0.32, 1] }}
      className="flex flex-col gap-2 py-4 first:pt-0 last:pb-0"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-label text-muted-foreground">
            {typeTitle(insight.type)}
            {insight.category !== insight.key && ` · ${insight.category}`}
          </p>
          <p className="mt-0.5 font-semibold">{insight.key}</p>
        </div>
        <Money value={{ minor: insight.impact_minor, currency: "GBP" }} className="text-heading tabular-nums" />
      </div>
      <p className="text-sm">
        <Explained text={insight.explanation} />
      </p>
      <p className="text-label text-muted-foreground">
        {confidenceWord(insight.confidence)}, from {n} {n === 1 ? "transaction" : "transactions"}
      </p>
      <div className="-mx-2 flex items-center gap-0.5">
        <button
          type="button"
          aria-label={`Useful: ${insight.key}`}
          aria-pressed={mark === "up"}
          disabled={busy}
          onClick={() => rate.mutate(true)}
          className={cn(round, mark === "up" && "bg-active")}
        >
          <ThumbsUp className="size-4" aria-hidden="true" />
        </button>
        <button
          type="button"
          aria-label={`Not useful: ${insight.key}`}
          aria-pressed={mark === "down"}
          disabled={busy}
          onClick={() => rate.mutate(false)}
          className={cn(round, mark === "down" && "bg-active")}
        >
          <ThumbsDown className="size-4" aria-hidden="true" />
        </button>
        <button
          type="button"
          aria-label={`Dismiss: ${insight.key}`}
          disabled={busy}
          onClick={() => hide.mutate()}
          className={cn(round, "ml-auto")}
        >
          <X className="size-4" aria-hidden="true" />
        </button>
      </div>
      {(rate.error || hide.error) && (
        <p role="alert" className="text-label text-warning-foreground">
          Couldn't save that. Try again.
        </p>
      )}
    </motion.li>
  );
}

/** The ranked Insights on the Overview: each explained, rateable and dismissable (#153). */
export function InsightsFeed() {
  const [asking, setAsking] = useState(false);
  const { data: raw } = useQuery({ queryKey: INSIGHTS_KEY, queryFn: fetchInsights });
  if (!Array.isArray(raw)) return null; // loading, or offline: the Overview says so elsewhere
  const data = raw.filter((i) => typeof i?.explanation === "string"); // an older server may answer otherwise
  return (
    <Card data-still aria-labelledby="insights-title">
      <div className="mb-4 flex items-baseline justify-between gap-3">
        <h2 id="insights-title" className="text-heading">
          Insights
        </h2>
        <div className="flex items-center gap-4">
          <a href="#briefing" className="inline-flex min-h-11 items-center text-sm font-semibold underline-offset-4 hover:underline">
            Weekly briefing
          </a>
          <button
            type="button"
            onClick={() => setAsking(true)}
            className="inline-flex min-h-11 items-center text-sm font-semibold underline-offset-4 hover:underline"
          >
            Check a purchase
          </button>
        </div>
      </div>
      {data.length ? (
        <ul className="fx-stagger divide-y divide-border">
          <AnimatePresence initial={false}>
            {data.map((i) => (
              <Row key={i.id} insight={i} />
            ))}
          </AnimatePresence>
        </ul>
      ) : (
        <p className="relative py-2 text-muted-foreground">
          <span className="fx-empty" aria-hidden="true" />
          Nothing worth your attention yet. Insights appear once there's enough history to compare against your own
          normal.
        </p>
      )}
      {asking && <WhatIfDialog onClose={() => setAsking(false)} />}
    </Card>
  );
}
