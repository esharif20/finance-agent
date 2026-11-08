import { useQuery } from "@tanstack/react-query";
import { fetchMonths, fetchStory } from "@/api/client";
import { Money } from "@/components/Money";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { categoryHref } from "@/lib/route";
import { MonthSwitcher } from "./MonthSwitcher";

/** The month's money story (#281): the worded recap, every figure the Engine computed, and links to the evidence. */
export function Story({ month }: { month?: string }) {
  const months = useQuery({ queryKey: ["months"], queryFn: fetchMonths });
  const current = month ?? months.data?.[0];
  const story = useQuery({ queryKey: ["story", current], queryFn: () => fetchStory(current as string), enabled: !!current });
  if (months.isError || story.isError) return <p role="alert">Couldn't load the story. Try again in a moment.</p>;
  if (months.data && !current) return <p className="text-muted-foreground">No months yet. Import some transactions first.</p>;
  if (!months.data || !story.data || !current) return <Skeleton className="h-[320px] rounded-card" aria-busy="true" />;
  const { recap, text, worded_by } = story.data;
  return (
    <div className="flex flex-col gap-6">
      <MonthSwitcher months={months.data} month={current} onChange={(m) => (window.location.hash = `#story/${m}`)} />
      <Card aria-labelledby="story-recap">
        <CardHeader>
          <CardTitle id="story-recap">The month in words</CardTitle>
        </CardHeader>
        <p>{text}</p>
        <p className="mt-3 text-label text-muted-foreground">
          {worded_by === "model"
            ? "Worded by the local model; every figure below comes from your Ledger."
            : "A plain summary, not worded by the model. Every figure below comes from your Ledger."}{" "}
          Evidence: {recap.evidence_id}
        </p>
      </Card>
      <Card aria-labelledby="story-changes">
        <CardHeader>
          <CardTitle id="story-changes">What changed</CardTitle>
        </CardHeader>
        {recap.changes.length ? (
          <ul className="divide-y divide-border">
            {recap.changes.map((c) => (
              <li key={c.category} className="py-4 first:pt-0 last:pb-0">
                <div className="flex items-baseline justify-between gap-3">
                  <a href={categoryHref(c.category, current)} className="font-semibold underline-offset-4 hover:underline">
                    {c.category}
                  </a>
                  <Money value={c.change} sign className="text-heading tabular-nums" />
                </div>
                <p className="text-label text-muted-foreground">
                  Spent <Money value={c.spent} /> against <Money value={c.expected} /> expected
                  {c.seasonal && ", a usual seasonal swing"}
                </p>
                {c.drivers.length > 0 && (
                  <ul className="mt-2 flex flex-col gap-1 text-sm">
                    {c.drivers.map((d) => (
                      <li key={d.merchant.data} className="flex justify-between gap-3">
                        <span>{d.merchant.data}</span>
                        <Money value={d.change} sign className="tabular-nums" />
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground">Nothing moved much against your own normal.</p>
        )}
      </Card>
      {recap.anomalies.length > 0 && (
        <Card aria-labelledby="story-anomalies">
          <CardHeader>
            <CardTitle id="story-anomalies">Worth a look</CardTitle>
          </CardHeader>
          <ul className="divide-y divide-border">
            {recap.anomalies.map((a) => (
              <li key={`${a.type}-${a.key.data}-${a.raised_on}`} className="flex justify-between gap-3 py-3 first:pt-0 last:pb-0">
                <span className="font-semibold">{a.key.data}</span>
                <Money value={a.impact} className="tabular-nums" />
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
