import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { animate, useReducedMotion } from "motion/react";
import { useDeferredValue, useEffect, useRef, useState } from "react";
import {
  NoData,
  fetchAccounts,
  fetchFxBalances,
  fetchFxMonth,
  fetchMonths,
  fetchSpending,
  fetchSummary,
} from "@/api/client";
import { ForecastPanel } from "@/components/ForecastPanel";
import { GuidanceCards } from "@/components/GuidanceCard";
import { InsightsFeed } from "@/components/InsightsFeed";
import { SubscriptionsCard } from "@/components/SubscriptionsCard";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { groupSpending } from "@/lib/categories";
import { accountHref, goHash } from "@/lib/route";
import { AccountStack, SELECT_SPRING } from "./AccountStack";
import { AccountDetails, AccountShares, TopMerchants, TransfersLine } from "./AccountView";
import { MonthBars, newestQuery, seriesQuery } from "./MonthBars";
import { MonthCard, monthLabel } from "./MonthCard";
import { MonthSwitcher } from "./MonthSwitcher";
import { SpendingDonut } from "./SpendingDonut";
import { Transactions } from "./Transactions";

// A month's figures change only through an import, a sync or a correction, and each of those
// invalidates every query; until then a switch back, or a prefetched one, is served from cache.
const FRESH = 30_000;
const DWELL_MS = 60; // how long a pointer rests on a card, bar or step before its view is fetched
const summaryQuery = (month: string | undefined, account: number | undefined) => ({
  queryKey: ["summary", month, account],
  queryFn: () => fetchSummary(month, account),
  staleTime: FRESH,
});
const spendingQuery = (month: string, account: number | undefined) => ({
  queryKey: ["spending", month, account],
  queryFn: () => fetchSpending(month, account),
  staleTime: FRESH,
});

/** Replays the settle (#59) when `view` changes, without a remount: remounting dropped the
 * subtree's query observers, so the list under it went blank until its refetch landed. */
function useSettle<T extends HTMLElement>(view: unknown, reduce: boolean | null) {
  const ref = useRef<T>(null);
  const last = useRef(view);
  useEffect(() => {
    if (last.current === view || !ref.current) return;
    last.current = view;
    const run = reduce
      ? animate(ref.current, { opacity: [0, 1] }, { duration: 0.15 })
      : animate(ref.current, { opacity: [0, 1], transform: ["translateY(12px)", "translateY(0px)"] }, SELECT_SPRING);
    return () => run.stop();
  }, [view, reduce]);
  return ref;
}

/** The Overview: the card stack chooses the Account in view (`account`, from the URL hash;
 * undefined is All accounts) and everything else follows it. */
export function Overview({ account }: { account?: number }) {
  const reduce = useReducedMotion();
  // undefined = the newest month; the switcher sets it. The bars and steppers show `picked` at once;
  // the rest of the view follows `chosen`, deferred, so the selection paints before that re-render
  const [picked, setChosen] = useState<string>();
  const [hot, setHot] = useState<number>(); // the Account a pointer or keyboard is on, lit in its share
  const chosen = useDeferredValue(picked);
  const client = useQueryClient();
  const summary = useQuery({ ...summaryQuery(chosen, account), placeholderData: keepPreviousData });
  const months = useQuery({ queryKey: ["months"], queryFn: fetchMonths });
  // the newest month (months are newest first, as the summary's default): spending need not wait for it
  const month = chosen ?? summary.data?.month ?? months.data?.[0];
  const spending = useQuery({
    ...spendingQuery(month as string, account),
    enabled: !!month,
    placeholderData: keepPreviousData,
  });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: fetchAccounts });
  // FX provenance and "rate needed" (#156), only once a foreign-currency Account exists
  const foreign = !!accounts.data?.some((a) => a.currency !== "GBP");
  const fx = useQuery({
    queryKey: ["fx-month", month, account],
    queryFn: () => fetchFxMonth(month as string, account),
    enabled: foreign && !!month,
    placeholderData: keepPreviousData,
    staleTime: FRESH,
  });
  const balances = useQuery({ queryKey: ["fx-balances"], queryFn: fetchFxBalances, enabled: foreign });
  // a hash naming an Account that isn't there (any more) shows All accounts
  const known = accounts.data?.some((a) => a.id === account) ? account : undefined;
  const select = (id: number | undefined) => {
    goHash(accountHref(id)); // a history entry, no navigation: Back returns to the last card
  };
  const stale = accounts.data !== undefined && account !== undefined && known === undefined;
  useEffect(() => {
    if (stale) window.location.replace(accountHref());
  }, [stale]);
  // Fetch what a switch to month `m` (undefined: the month in view, keyed as the summary query
  // keys it) of Account `a` shows, so its click paints from cache.
  const ahead = (m: string | undefined, a: number | undefined) => {
    const at = m ?? month;
    if (!at) return;
    void client.prefetchQuery(summaryQuery(m ?? chosen, a));
    void client.prefetchQuery(spendingQuery(at, a));
    if (a !== account) {
      void client.prefetchQuery(seriesQuery(a));
      void client.prefetchQuery(newestQuery(a));
    }
  };
  // A pointer resting on a card, a bar or a step fetches ahead; one sweeping across the bars
  // on its way somewhere else fetches nothing, so it never floods the server.
  const dwell = useRef(0);
  useEffect(() => () => clearTimeout(dwell.current), []);
  const preview = (m: string | undefined, a: number | undefined) => {
    clearTimeout(dwell.current);
    dwell.current = window.setTimeout(() => ahead(m, a), DWELL_MS);
  };
  // Once this view has landed, fetch the month before it: the step people take next.
  const landed = summary.isSuccess && !summary.isPlaceholderData && spending.isSuccess && !spending.isPlaceholderData;
  const older = months.data?.[months.data.indexOf(summary.data?.month ?? "") + 1];
  useEffect(() => {
    if (landed && older) ahead(older, account);
  }, [landed, older, account]); // ahead is a fresh closure each render, over the same client
  const view = account ?? "all";
  const side = useSettle<HTMLDivElement>(view, reduce);
  const top = useSettle<HTMLDivElement>(view, reduce);
  const list = useSettle<HTMLElement>(view, reduce);

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
          data-magnetic className="mt-4 min-h-11 rounded-full bg-primary px-5 text-sm font-semibold text-primary-foreground"
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
  // The month just chosen shows on the steppers at once, so a second click steps on from it;
  // the figures keep the last month's, dimmed, until the new ones land.
  const shown = picked ?? summary.data.month;
  const waiting = summary.isPlaceholderData || spending.isPlaceholderData;
  // The view under the stack settles with the card (useSettle): same spring, a short rise, no
  // travel under reduced motion.
  return (
    <div className="flex flex-col gap-6">
      <MonthSwitcher months={months.data} month={shown} onChange={setChosen} onPreview={(m) => preview(m, account)} />
      <MonthBars month={shown} account={account} onChange={setChosen} onPreview={(m) => preview(m, account)} />
      <div className="grid items-start gap-6 lg:grid-cols-[7fr_5fr]">
        {/* first in the DOM: on a phone the view sits under the card stack */}
        <div className="flex flex-col gap-6 lg:col-start-2 lg:row-start-1">
          <AccountStack
            accounts={accounts.data}
            balances={balances.data}
            selected={account}
            onSelect={select}
            onPreview={(id) => preview(undefined, id)}
            onHot={setHot}
          />
          {/* relative: paints above the stack's WebGL canvas, which overflows below the cards */}
          <div ref={side} className="relative">
            {one ? (
              <AccountDetails account={one} />
            ) : (
              <AccountShares summary={summary.data} accounts={accounts.data} hot={hot} onHot={setHot} />
            )}
          </div>
        </div>
        <div className="flex flex-col gap-6 lg:col-start-1 lg:row-start-1">
          <div className="waiting flex flex-col gap-6" aria-busy={waiting} data-waiting={waiting}>
            <MonthCard summary={summary.data} slices={slices} fx={fx.data}>
              <TransfersLine summary={summary.data} combined={account === undefined} />
            </MonthCard>
            <SpendingDonut spending={spending.data} account={account} />
          </div>
          <GuidanceCards />
          <InsightsFeed />
          <ForecastPanel />
          <SubscriptionsCard />
          <div ref={top} className="waiting" data-waiting={waiting}>
            <TopMerchants spending={spending.data} />
          </div>
        </div>
      </div>
      <section ref={list} aria-labelledby="txns-title" className="flex flex-col gap-4">
        <h2 id="txns-title" className="text-heading">
          {one ? `${one.bank} ${one.masked_number}` : "All accounts"}: transactions in{" "}
          {monthLabel(summary.data.month)}
        </h2>
        <Transactions
          scope={{ account_id: account === undefined ? undefined : String(account), month: summary.data.month }}
          page={20}
        />
      </section>
    </div>
  );
}
