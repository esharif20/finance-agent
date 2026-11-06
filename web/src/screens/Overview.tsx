import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { NoData, fetchAccounts, fetchMonths, fetchSpending, fetchSummary } from "@/api/client";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { AccountStack } from "./AccountStack";
import { groupSpending } from "@/lib/categories";
import { MonthBars } from "./MonthBars";
import { MonthCard } from "./MonthCard";
import { MonthSwitcher } from "./MonthSwitcher";
import { SpendingDonut } from "./SpendingDonut";

export function Overview() {
  // undefined = the newest month; the switcher sets it
  const [chosen, setChosen] = useState<string>();
  const [account, setAccount] = useState<number>(); // undefined = All accounts
  const summary = useQuery({
    queryKey: ["summary", chosen, account],
    queryFn: () => fetchSummary(chosen, account),
    placeholderData: keepPreviousData,
  });
  const month = chosen ?? summary.data?.month;
  const spending = useQuery({
    queryKey: ["spending", month, account],
    queryFn: () => fetchSpending(month as string, account),
    enabled: !!month,
    placeholderData: keepPreviousData,
  });
  const months = useQuery({ queryKey: ["months"], queryFn: fetchMonths });
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
  if (summary.error || accounts.error || spending.error || months.error) {
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
            spending.refetch();
            months.refetch();
          }}
          className="mt-4 min-h-11 rounded-full bg-primary px-5 text-sm font-semibold text-primary-foreground"
        >
          Try again
        </button>
      </Card>
    );
  }
  if (!summary.data || !accounts.data || !spending.data || !months.data) {
    return (
      <div className="grid gap-6 lg:grid-cols-[7fr_5fr]" aria-busy="true">
        <Skeleton className="h-[440px] rounded-card" />
        <Skeleton className="h-[240px] rounded-card" />
      </div>
    );
  }
  const { slices } = groupSpending(spending.data.categories);
  return (
    <div className="flex flex-col gap-6">
      <MonthSwitcher months={months.data} month={summary.data.month} onChange={setChosen} />
      <MonthBars month={summary.data.month} account={account} onChange={setChosen} />
      <div className="grid items-start gap-6 lg:grid-cols-[7fr_5fr]">
        <div className="flex flex-col gap-6">
          <MonthCard summary={summary.data} slices={slices} />
          <SpendingDonut spending={spending.data} />
        </div>
        <AccountStack accounts={accounts.data} selected={account} onSelect={setAccount} />
      </div>
    </div>
  );
}
