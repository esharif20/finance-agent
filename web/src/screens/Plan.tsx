import { useQuery } from "@tanstack/react-query";
import { type AllocationPlan, fetchAllocationPlan } from "@/api/client";
import { Money } from "@/components/Money";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

type Line = AllocationPlan["lines"][number];

// Advice wording only: each line says why the Engine gave it that amount.
const REASON: Record<Line["reason"], string> = {
  fixed_bill: "A bill due this month, so it comes first.",
  on_pattern: "In line with what you usually spend here.",
  goal_floor: "The least that keeps your goal on track.",
  constraint: "Held back by a limit you set.",
  priority: "Given more because you said it matters.",
  leftover: "What is left once everything else is covered.",
  baseline: "The 50/30/20 split.",
};

const month = (m: string) =>
  new Date(`${m}-01T00:00:00Z`).toLocaleDateString("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });

function Lines({ lines, id }: { lines: Line[]; id: string }) {
  return (
    <ul aria-labelledby={id} className="divide-y divide-border">
      {lines.map((l) => (
        <li key={`${l.kind}-${l.name.data}`} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-3">
          <span>{l.name.data}</span>
          <span className="text-heading tabular-nums">
            <Money value={l.amount} />
          </span>
          <p className="basis-full text-label text-muted-foreground">{REASON[l.reason]}</p>
        </li>
      ))}
    </ul>
  );
}

/** The month's Allocation Plan (#273): the Engine's lines with each trade-off reason, beside the 50/30/20 baseline. Observe only. */
export function Plan() {
  const { data, isError } = useQuery({ queryKey: ["allocation"], queryFn: fetchAllocationPlan });
  if (isError) return <p role="alert">Couldn't load your plan. Try again in a moment.</p>;
  if (!data) return <Skeleton className="h-[240px] rounded-card" aria-busy="true" />;
  if (data.status !== "ok")
    return (
      <Card aria-labelledby="plan-none">
        <CardHeader>
          <CardTitle id="plan-none">{data.status === "infeasible" ? "No plan fits your limits" : "Not enough history yet"}</CardTitle>
        </CardHeader>
        <p>
          {data.status === "infeasible"
            ? (data.infeasible_reason ?? "No plan satisfies every limit you have set.")
            : "There isn't enough spending history to build a plan for this month yet."}
        </p>
      </Card>
    );
  return (
    <div className="flex flex-col gap-6">
      <p className="text-muted-foreground">
        {data.month && `${month(data.month)}. `}
        {data.income && (
          <>
            Planned around <Money value={data.income} /> of income.
          </>
        )}
      </p>
      <div className="grid items-start gap-6 lg:grid-cols-2">
        <Card aria-labelledby="plan-lines">
          <CardHeader>
            <CardTitle id="plan-lines">Your plan</CardTitle>
          </CardHeader>
          <Lines lines={data.lines} id="plan-lines" />
        </Card>
        <Card aria-labelledby="plan-base">
          <CardHeader>
            <CardTitle id="plan-base">50/30/20 for comparison</CardTitle>
          </CardHeader>
          <Lines lines={data.baseline} id="plan-base" />
        </Card>
      </div>
    </div>
  );
}
