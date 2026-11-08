import { useQuery } from "@tanstack/react-query";
import { type Goals as GoalsData, fetchGoals } from "@/api/client";
import { Money } from "@/components/Money";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

type Goal = GoalsData["goals"][number];

const day = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const gbp = (minor: number) => ({ minor, currency: "GBP" });

const STATUS: Record<Goal["status"], string> = {
  reached: "Reached",
  overdue: "Past its date",
  at_risk: "At risk",
  on_track: "On track",
  unchecked: "No plan to check against",
};

function GoalCard({ g }: { g: Goal }) {
  const pct = Math.min(100, Math.round((Math.max(0, g.saved_minor) / g.amount_minor) * 100));
  return (
    <li>
      <Card aria-labelledby={`goal-${g.id}`}>
        <CardHeader>
          <CardTitle id={`goal-${g.id}`}>{g.text}</CardTitle>
          <span className={cn("text-label", g.status === "at_risk" || g.status === "overdue" ? "text-warning-foreground" : "text-muted-foreground")}>
            {STATUS[g.status]}
          </span>
        </CardHeader>
        <div
          role="progressbar"
          aria-label={`${g.text} progress`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct}
          className="mb-3 h-2 overflow-hidden rounded-full bg-active"
        >
          <div className="h-full rounded-full bg-foreground" style={{ width: `${pct}%` }} />
        </div>
        <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-2">
          <dt className="text-muted-foreground">{g.estimated ? "Saved so far (estimated)" : "Saved so far"}</dt>
          <dd className="tabular-nums">
            <Money value={gbp(g.saved_minor)} /> of <Money value={gbp(g.amount_minor)} />
          </dd>
          <dt className="text-muted-foreground">Due</dt>
          <dd>{day(g.due_on)}</dd>
          {g.weekly_target_minor !== null && g.weekly_target_minor > 0 && (
            <>
              <dt className="text-muted-foreground">To save each week</dt>
              <dd className="text-heading tabular-nums">
                <Money value={gbp(g.weekly_target_minor)} />
              </dd>
            </>
          )}
          {g.planned_weekly_minor !== null && (
            <>
              <dt className="text-muted-foreground">Your plan sets aside</dt>
              <dd className="tabular-nums">
                <Money value={gbp(g.planned_weekly_minor)} /> a week
              </dd>
            </>
          )}
        </dl>
        {g.status === "at_risk" && g.shortfall_minor !== null && (
          <p className="mt-3 text-sm">
            The plan is about <Money value={gbp(g.shortfall_minor)} /> a week short of this goal.
          </p>
        )}
        {g.estimated && (
          <p className="mt-3 text-label text-muted-foreground">No account is linked, so progress is estimated from your plan.</p>
        )}
      </Card>
    </li>
  );
}

/** Coaching goals (#278): weekly target, progress and at-risk state, and the payday check-in. Observe only. */
export function Goals() {
  const { data, isError } = useQuery({ queryKey: ["goals"], queryFn: fetchGoals });
  if (isError) return <p role="alert">Couldn't load your goals. Try again in a moment.</p>;
  if (!data) return <Skeleton className="h-[240px] rounded-card" aria-busy="true" />;
  return (
    <div className="flex flex-col gap-6">
      {data.checkin && (
        <Card aria-labelledby="goals-checkin">
          <CardHeader>
            <CardTitle id="goals-checkin">{data.checkin.title}</CardTitle>
          </CardHeader>
          <p>{data.checkin.body}</p>
        </Card>
      )}
      {data.goals.length ? (
        <ul className="grid items-start gap-6 lg:grid-cols-2">
          {data.goals.map((g) => (
            <GoalCard key={g.id} g={g} />
          ))}
        </ul>
      ) : (
        <p className="text-muted-foreground">
          No goals yet. Tell the Assistant what you're saving for, how much and by when, and confirm it to see it here.
        </p>
      )}
    </div>
  );
}
