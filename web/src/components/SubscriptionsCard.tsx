import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Repeat } from "lucide-react";
import { fetchSubscriptionCosts } from "@/api/client";
import { Money } from "@/components/Money";
import { SUBS_KEY } from "@/components/SubscriptionPrompt";
import { Card } from "@/components/ui/card";
import { flagCount } from "@/lib/subscriptions";

const SHORT = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });

/** The phone's quick way in, under the account cards: the monthly Subscriptions total as the API
    gives it and the soonest next charge it lists (a pick, no maths); a tap opens Subscriptions. */
export function SubscriptionsStrip() {
  const { data } = useQuery({ queryKey: [...SUBS_KEY, "costs"], queryFn: fetchSubscriptionCosts });
  if (!data || !Array.isArray(data.items) || !data.items.length) return null;
  const subs = data.totals.find((t) => t.kind === "subscription")?.monthly;
  const next = data.items
    .filter((i) => i.next_due_on)
    .sort((a, b) => (a.next_due_on as string).localeCompare(b.next_due_on as string))[0];
  return (
    <Card className="p-0 sm:p-0">
      <a
        href="#subscriptions"
        className="subs-strip flex min-h-14 items-center gap-3 rounded-card px-4 py-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="grid size-9 shrink-0 place-items-center rounded-full bg-active" aria-hidden="true">
          <Repeat className="size-4" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-label font-semibold">Subscriptions</span>
          {next?.next_due_on && (
            <span className="block truncate text-meta text-muted-foreground">
              Next: {next.display_name || next.merchant}, {SHORT.format(new Date(`${next.next_due_on}T00:00:00`))}
            </span>
          )}
        </span>
        {subs && (
          <span className="text-right">
            <Money value={subs} className="block text-row font-semibold tabular-nums" />
            <span className="block text-meta text-muted-foreground">a month</span>
          </span>
        )}
        <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      </a>
    </Card>
  );
}

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
