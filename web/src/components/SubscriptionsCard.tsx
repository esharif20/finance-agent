import { useQuery } from "@tanstack/react-query";
import { fetchSubscriptionCosts } from "@/api/client";
import { Money } from "@/components/Money";
import { SUBS_KEY } from "@/components/SubscriptionPrompt";
import { Card } from "@/components/ui/card";
import { flagCount } from "@/lib/subscriptions";

/** The Overview card: what Subscriptions cost a month, what Bills cost, and how many flags. */
export function SubscriptionsCard() {
  const { data } = useQuery({ queryKey: [...SUBS_KEY, "costs"], queryFn: fetchSubscriptionCosts });
  if (!data || !Array.isArray(data.items)) return null; // loading, offline, or nothing to show yet
  const total = (kind: string) => data.totals.find((t) => t.kind === kind)?.monthly;
  const subs = total("subscription");
  const bills = total("bill");
  const flags = flagCount(data);
  const likely = data.likely_monthly?.minor ? data.likely_monthly : null; // #310: kept apart from the total
  if (!data.items.length)
    return (
      <Card aria-labelledby="subs-card">
        <h2 id="subs-card" className="text-heading">
          Subscriptions
        </h2>
        <p className="mt-2 text-muted-foreground">Nothing recurring found yet.</p>
        <a href="#subscriptions" className="mt-3 inline-block text-sm font-semibold underline-offset-4 hover:underline">
          Open Subscriptions
        </a>
      </Card>
    );
  return (
    <Card aria-labelledby="subs-card">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="subs-card" className="text-heading">
          Subscriptions
        </h2>
        <a href="#subscriptions" className="inline-flex min-h-11 items-center text-sm font-semibold underline-offset-4 hover:underline">
          See all
        </a>
      </div>
      {subs && (
        <p className="mt-1">
          <Money value={subs} countUp className="text-title tabular-nums" />
          <span className="text-label text-muted-foreground"> a month</span>
        </p>
      )}
      {bills && (
        <p className="mt-2 text-label text-muted-foreground">
          Bills <Money value={bills} /> a month
        </p>
      )}
      {likely && (
        <p className="text-label text-muted-foreground">
          Likely (new) <Money value={likely} /> a month, not in the total
        </p>
      )}
      {flags !== null && (
        <p className="text-label text-muted-foreground">
          {flags} {flags === 1 ? "flag" : "flags"}
        </p>
      )}
    </Card>
  );
}
