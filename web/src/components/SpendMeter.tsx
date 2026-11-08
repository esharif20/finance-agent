import { useQuery } from "@tanstack/react-query";
import type { components } from "@/api/schema";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { formatMoney } from "@/lib/money";

type Bucket = components["schemas"]["BucketOut"];

const gbp = (minor: number) => formatMoney({ minor, currency: "GBP" });

/** Cloud spend per bucket against its monthly cap (#304). The state (warn from 80%) comes from the server. */
export function SpendMeter() {
  const { data } = useQuery({
    queryKey: ["spend"],
    queryFn: async () => {
      const res = await fetch("/api/spend");
      if (!res.ok) throw new Error(String(res.status));
      return (await res.json()) as Bucket[];
    },
    retry: false,
  });
  if (!data?.length) return null;
  return (
    <Card aria-labelledby="know-spend">
      <CardHeader>
        <CardTitle id="know-spend">Cloud spend this month</CardTitle>
      </CardHeader>
      <ul className="divide-y divide-border">
        {data.map((b) => {
          const pct = b.cap_pence ? Math.min(100, ((b.spent_pence + b.reserved_pence) / b.cap_pence) * 100) : 100;
          return (
            <li key={b.bucket} className="py-3 first:pt-0 last:pb-0">
              <div className="flex flex-wrap justify-between gap-2 text-sm">
                <span className="font-semibold">{b.bucket.replace(/_/g, " ")}</span>
                <span className="tabular-nums">
                  {gbp(b.spent_pence)} of {gbp(b.cap_pence)}
                </span>
              </div>
              <div role="progressbar" aria-label={`${b.bucket} spend`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)} className="mt-2 h-2 overflow-hidden rounded-full bg-active">
                <div className={`h-full rounded-full ${b.state === "ok" ? "bg-primary" : "bg-warning-foreground"}`} style={{ width: `${pct}%` }} />
              </div>
              {b.state !== "ok" && (
                <p role="status" className="mt-1 text-label text-warning-foreground">
                  {b.state === "capped" ? "Cap reached. This stays on this Mac until next month." : "Past 80% of this month's cap."}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
