import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type RegretItem, answerRegret, fetchBriefing } from "@/api/client";
import { Explained } from "@/components/InsightsFeed";
import { Money } from "@/components/Money";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { typeTitle } from "@/lib/insights";
import { cn } from "@/lib/utils";

export const BRIEFING_KEY = ["briefing"] as const;

const day = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });

const choice =
  "inline-flex min-h-11 items-center rounded-full px-4 text-sm font-semibold transition-[background-color,transform] duration-150 ease-out active:scale-[0.97] [@media(hover:hover)]:hover:bg-active disabled:opacity-50";

function Regret({ item }: { item: RegretItem }) {
  const client = useQueryClient();
  const save = useMutation({
    mutationFn: (worth: boolean) => answerRegret(item.transaction_id, worth),
    onSettled: () => client.invalidateQueries({ queryKey: BRIEFING_KEY }),
  });
  const name = item.merchant ?? item.description;
  const mark = save.isPending ? save.variables : item.worth_it;
  return (
    <li className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 py-4 first:pt-0 last:pb-0">
      <div className="min-w-0">
        <p className="font-semibold">{name}</p>
        <p className="text-label text-muted-foreground">
          {day(item.posted_on)} · <Money value={item.amount} />
        </p>
      </div>
      <div className="-mx-2 flex items-center gap-1" role="group" aria-label={`Was ${name} worth it?`}>
        <button
          type="button"
          aria-pressed={mark === true}
          disabled={save.isPending}
          onClick={() => save.mutate(true)}
          className={cn(choice, mark === true && "bg-active")}
        >
          Worth it
        </button>
        <button
          type="button"
          aria-pressed={mark === false}
          disabled={save.isPending}
          onClick={() => save.mutate(false)}
          className={cn(choice, mark === false && "bg-active")}
        >
          Not worth it
        </button>
      </div>
      {save.error && (
        <p role="alert" className="w-full text-label text-warning-foreground">
          Couldn't save that. Try again.
        </p>
      )}
    </li>
  );
}

/** This week at a glance: top Insights, the month's spending band, renewals and the regret review (#154). */
export function Briefing() {
  const { data, isError } = useQuery({ queryKey: BRIEFING_KEY, queryFn: fetchBriefing });
  if (isError) return <p role="alert">Couldn't load the briefing. Try again in a moment.</p>;
  if (!data) return <Skeleton className="h-[320px] rounded-card" aria-busy="true" />;
  const f = data.forecast;
  const left = data.regret.filter((r) => r.worth_it === null).length;
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="text-muted-foreground">
          Week of {day(data.week)}
          {data.next_payday && <> · next payday {day(data.next_payday)}</>}
        </p>
        <a href="#know" className="inline-flex min-h-11 items-center text-sm font-semibold underline-offset-4 hover:underline">
          How well I know you
        </a>
      </div>
      <div className="grid items-start gap-6 lg:grid-cols-2">
        <Card aria-labelledby="brief-insights">
          <CardHeader>
            <CardTitle id="brief-insights">Worth your attention</CardTitle>
          </CardHeader>
          {data.insights.length ? (
            <ul className="divide-y divide-border">
              {data.insights.map((i) => (
                <li key={i.id} className="py-4 first:pt-0 last:pb-0">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-label text-muted-foreground">{typeTitle(i.type)}</p>
                      <p className="font-semibold">{i.key}</p>
                    </div>
                    <Money value={{ minor: i.impact_minor, currency: "GBP" }} className="text-heading tabular-nums" />
                  </div>
                  <p className="mt-1 text-sm">
                    <Explained text={i.explanation} />
                  </p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-muted-foreground">
              Nothing yet. Insights appear once there's enough history to compare against your own normal.
            </p>
          )}
        </Card>
        <div className="flex flex-col gap-6">
          <Card aria-labelledby="brief-month">
            <CardHeader>
              <CardTitle id="brief-month">This month's spending</CardTitle>
            </CardHeader>
            {f ? (
              <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-2">
                <dt className="text-muted-foreground">Expected</dt>
                <dd className="text-heading tabular-nums">
                  <Money value={{ minor: f.point_minor, currency: "GBP" }} />
                </dd>
                <dt className="text-muted-foreground">Likely range</dt>
                <dd className="tabular-nums">
                  <Money value={{ minor: f.lower_minor, currency: "GBP" }} /> to{" "}
                  <Money value={{ minor: f.upper_minor, currency: "GBP" }} />
                </dd>
                <dt className="text-muted-foreground">Already scheduled</dt>
                <dd className="tabular-nums">
                  <Money value={{ minor: f.recurring_minor, currency: "GBP" }} />
                </dd>
              </dl>
            ) : (
              <p className="text-muted-foreground">Not enough history to forecast yet.</p>
            )}
            {f && f.left_out > 0 && (
              <p className="mt-3 text-label text-muted-foreground">
                {f.left_out} {f.left_out === 1 ? "category has" : "categories have"} too little history and{" "}
                {f.left_out === 1 ? "is" : "are"} left out.
              </p>
            )}
          </Card>
          <Card aria-labelledby="brief-renewals">
            <CardHeader>
              <CardTitle id="brief-renewals">Coming up</CardTitle>
            </CardHeader>
            {data.renewals.length ? (
              <ul className="divide-y divide-border">
                {data.renewals.map((r) => (
                  <li key={r.insight_id} className="flex items-baseline justify-between gap-3 py-3 first:pt-0 last:pb-0">
                    <span>
                      <span className="font-semibold">{r.merchant}</span>{" "}
                      <span className="text-muted-foreground">renews {day(r.due_on)}</span>
                    </span>
                    <Money value={{ minor: r.amount_minor, currency: "GBP" }} className="tabular-nums" />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-muted-foreground">No renewals due this week.</p>
            )}
          </Card>
        </div>
      </div>
      <Card aria-labelledby="brief-regret">
        <CardHeader>
          <CardTitle id="brief-regret">Was it worth it?</CardTitle>
          {data.regret.length > 0 && (
            <span className="text-label text-muted-foreground">{left === 0 ? "All answered" : `${left} to answer`}</span>
          )}
        </CardHeader>
        {data.regret.length ? (
          <ul className="divide-y divide-border">
            {data.regret.map((r) => (
              <Regret key={r.transaction_id} item={r} />
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground">Nothing to look back on this week.</p>
        )}
        <p className="mt-4 text-label text-muted-foreground">
          Your answers tune what I flag. They are kept as feedback, never as memories, and you can wipe them any time.
        </p>
      </Card>
    </div>
  );
}
