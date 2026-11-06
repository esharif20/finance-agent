import { useQuery } from "@tanstack/react-query";
import { NoData, fetchAccounts, fetchSummary } from "@/api/client";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { AccountsCard } from "./AccountsCard";
import { MonthCard } from "./MonthCard";

export function Overview() {
  const summary = useQuery({ queryKey: ["summary"], queryFn: () => fetchSummary() });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: fetchAccounts });

  if (summary.error instanceof NoData) {
    return (
      <Card>
        <h2 className="text-[18px] font-semibold">No transactions yet</h2>
        <p className="mt-2 text-muted-foreground">
          Import a statement export with <code>fa import</code>, then reload this page.
        </p>
      </Card>
    );
  }
  if (summary.error || accounts.error) {
    return (
      <Card role="alert">
        <h2 className="text-[18px] font-semibold">Can't reach the local server</h2>
        <p className="mt-2 text-muted-foreground">
          Run <code>fa</code> in a terminal to start it, then try again.
        </p>
        <button
          type="button"
          onClick={() => {
            summary.refetch();
            accounts.refetch();
          }}
          className="mt-4 min-h-11 rounded-full bg-primary px-5 text-sm font-semibold text-primary-foreground"
        >
          Try again
        </button>
      </Card>
    );
  }
  if (!summary.data || !accounts.data) {
    return (
      <div className="grid gap-6 lg:grid-cols-[7fr_5fr]" aria-busy="true">
        <Skeleton className="h-[440px] rounded-card" />
        <Skeleton className="h-[240px] rounded-card" />
      </div>
    );
  }
  return (
    <div className="grid items-start gap-6 lg:grid-cols-[7fr_5fr]">
      <MonthCard summary={summary.data} />
      <AccountsCard accounts={accounts.data} />
    </div>
  );
}
