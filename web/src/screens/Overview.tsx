import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { motion, useReducedMotion } from "motion/react";
import { useEffect, useState } from "react";
import {
  NoData,
  fetchAccounts,
  fetchFxBalances,
  fetchFxMonth,
  fetchMonths,
  fetchSpending,
  fetchSummary,
} from "@/api/client";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { groupSpending } from "@/lib/categories";
import { accountHref } from "@/lib/route";
import { AccountStack, SELECT_SPRING } from "./AccountStack";
import { AccountDetails, AccountShares, TopMerchants, TransfersLine } from "./AccountView";
import { MonthBars } from "./MonthBars";
import { MonthCard, monthLabel } from "./MonthCard";
import { MonthSwitcher } from "./MonthSwitcher";
import { SpendingDonut } from "./SpendingDonut";
import { Transactions } from "./Transactions";

/** The Overview: the card stack chooses the Account in view (`account`, from the URL hash;
 * undefined is All accounts) and everything else follows it. */
export function Overview({ account }: { account?: number }) {
  const reduce = useReducedMotion();
  // undefined = the newest month; the switcher sets it
  const [chosen, setChosen] = useState<string>();
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
  // FX provenance and "rate needed" (#156), only once a foreign-currency Account exists
  const foreign = !!accounts.data?.some((a) => a.currency !== "GBP");
  const fx = useQuery({
    queryKey: ["fx-month", month, account],
    queryFn: () => fetchFxMonth(month as string, account),
    enabled: foreign && !!month,
    placeholderData: keepPreviousData,
  });
  const balances = useQuery({ queryKey: ["fx-balances"], queryFn: fetchFxBalances, enabled: foreign });
  // a hash naming an Account that isn't there (any more) shows All accounts
  const known = accounts.data?.some((a) => a.id === account) ? account : undefined;
  const select = (id: number | undefined) => {
    window.location.hash = accountHref(id); // a history entry: Back returns to the last card
  };
  const stale = accounts.data !== undefined && account !== undefined && known === undefined;
  useEffect(() => {
    if (stale) window.location.replace(accountHref());
  }, [stale]);

  if (summary.error instanceof NoData && account === undefined) {
    return (
      <Card>
        <h2 className="text-heading">No transactions yet</h2>
        <p className="mt-2 text-muted-foreground">
          Import a statement export with <code>fa import</code>, then reload this page.
        </p>
      </Card>
    );
  }
  if (!stale && (summary.error || accounts.error || spending.error || months.error)) {
    return (
      <Card role="alert">
        <h2 className="text-heading">Can't reach the local server</h2>
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
  if (stale || !summary.data || !accounts.data || !spending.data || !months.data) {
    return (
      <div className="grid gap-6 lg:grid-cols-[7fr_5fr]" aria-busy="true">
        <Skeleton className="h-[440px] rounded-card" />
        <Skeleton className="h-[240px] rounded-card" />
      </div>
    );
  }
  const { slices } = groupSpending(spending.data.categories);
  const one = accounts.data.find((a) => a.id === account);
  // The view under the stack settles with the card: same spring, a short rise, no travel
  // under reduced motion. Keyed by Account so a switch replays it.
  const view = account ?? "all";
  const settle = {
    initial: { opacity: 0, y: reduce ? 0 : 12 },
    animate: { opacity: 1, y: 0 },
    transition: reduce ? { duration: 0.15 } : SELECT_SPRING,
  };
  return (
    <div className="flex flex-col gap-6">
      <MonthSwitcher months={months.data} month={summary.data.month} onChange={setChosen} />
      <MonthBars month={summary.data.month} account={account} onChange={setChosen} />
      <div className="grid items-start gap-6 lg:grid-cols-[7fr_5fr]">
        {/* first in the DOM: on a phone the view sits under the card stack */}
        <div className="flex flex-col gap-6 lg:col-start-2 lg:row-start-1">
          <AccountStack accounts={accounts.data} balances={balances.data} selected={account} onSelect={select} />
          {/* relative: paints above the stack's WebGL canvas, which overflows below the cards */}
          <motion.div key={view} {...settle} className="relative">
            {one ? (
              <AccountDetails account={one} />
            ) : (
              <AccountShares summary={summary.data} accounts={accounts.data} />
            )}
          </motion.div>
        </div>
        <div className="flex flex-col gap-6 lg:col-start-1 lg:row-start-1">
          <MonthCard summary={summary.data} slices={slices} fx={fx.data}>
            <TransfersLine summary={summary.data} combined={account === undefined} />
          </MonthCard>
          <SpendingDonut spending={spending.data} account={account} />
          <motion.div key={view} {...settle}>
            <TopMerchants spending={spending.data} />
          </motion.div>
        </div>
      </div>
      <motion.section key={view} {...settle} aria-labelledby="txns-title" className="flex flex-col gap-4">
        <h2 id="txns-title" className="text-heading">
          {one ? `${one.bank} ${one.masked_number}` : "All accounts"}: transactions in{" "}
          {monthLabel(summary.data.month)}
        </h2>
        <Transactions
          key={`${account ?? "all"}-${summary.data.month}`}
          scope={{ account_id: account === undefined ? undefined : String(account), month: summary.data.month }}
          page={20}
        />
      </motion.section>
    </div>
  );
}
