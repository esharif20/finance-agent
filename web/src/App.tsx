import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Eye, EyeOff, Inbox, MessageCircle, LayoutDashboard, ListOrdered, Repeat, Settings as SettingsIcon } from "lucide-react";
import { Ambient } from "@/components/Ambient";
import { BackupStatus } from "@/components/BackupStatus";
import { IconSwap } from "@/components/IconSwap";
import { NavTabs } from "@/components/NavTabs";
import { SyncNow } from "@/components/SyncNow";
import { PrivacyProvider, usePrivacy } from "@/lib/privacy";
import { useRoute } from "@/lib/route";
import { Assistant } from "@/screens/Assistant";
import { CategoryScreen } from "@/screens/CategoryScreen";
import { Overview } from "@/screens/Overview";
import { ReviewQueue, useReviewCount } from "@/screens/ReviewQueue";
import { Settings } from "@/screens/Settings";
import { Subscriptions } from "@/screens/Subscriptions";
import { Transactions } from "@/screens/Transactions";

const queryClient = new QueryClient();

function Mark() {
  // three bars of decreasing length: money being sorted
  return (
    <svg viewBox="0 0 32 32" className="size-8" aria-hidden="true">
      <rect width="32" height="32" rx="9" className="fill-primary" />
      <rect x="7" y="8" width="18" height="4" rx="2" className="fill-primary-foreground" />
      <rect x="7" y="14" width="13" height="4" rx="2" className="fill-primary-foreground" />
      <rect x="7" y="20" width="8" height="4" rx="2" className="fill-primary-foreground" />
    </svg>
  );
}

function HideAmounts() {
  const { hidden, toggle } = usePrivacy();
  return (
    <button
      type="button"
      aria-pressed={hidden}
      onClick={toggle}
      className="inline-flex min-h-11 items-center gap-2 rounded-full glass px-4 text-sm font-semibold transition-colors [@media(hover:hover)]:hover:bg-active"
    >
      <IconSwap on={hidden} a={<Eye className="size-4" />} b={<EyeOff className="size-4" />} />
      Hide amounts
      <kbd className="hidden rounded-md bg-raised px-1.5 text-xs text-muted-foreground sm:inline">H</kbd>
    </button>
  );
}

const tabs = (review: number) => [
  { id: "overview", label: "Overview", icon: <LayoutDashboard className="size-4" aria-hidden="true" /> },
  { id: "transactions", label: "Transactions", icon: <ListOrdered className="size-4" aria-hidden="true" /> },
  { id: "subscriptions", label: "Subscriptions", icon: <Repeat className="size-4" aria-hidden="true" /> },
  { id: "assistant", label: "Assistant", icon: <MessageCircle className="size-4" aria-hidden="true" /> },
  { id: "review", label: "Review", icon: <Inbox className="size-4" aria-hidden="true" />, badge: review },
];

function Shell() {
  const route = useRoute();
  const review = useReviewCount();
  const TABS = tabs(review);
  const tab = route.name === "transactions" || route.name === "review" || route.name === "subscriptions" || route.name === "assistant" ? route.name : route.name === "settings" ? "" : "overview";
  return (
    <>
      <Ambient />
      <div className="mx-auto w-full max-w-[1120px] px-4 pt-3 pb-28 sm:px-9 sm:pb-16">
        <header className="glass sticky top-3 z-20 mb-6 flex items-center gap-3 rounded-full py-2 pr-2 pl-3">
          <Mark />
          <span className="text-base font-semibold">Finance Agent</span>
          <NavTabs
          tabs={TABS}
            tab={tab}
            className="ml-4 hidden gap-1 sm:flex"
            item="inline-flex min-h-11 items-center gap-2 rounded-full px-4 text-sm font-semibold"
          />
          <SyncNow />
          <a
            href="#settings"
            aria-label="Settings"
            aria-current={route.name === "settings" ? "page" : undefined}
            className={`inline-flex size-11 shrink-0 items-center justify-center rounded-full transition-colors [@media(hover:hover)]:hover:bg-active ${route.name === "settings" ? "bg-foreground/10" : ""}`}
          >
            <SettingsIcon className="size-4" aria-hidden="true" />
          </a>
          <HideAmounts />
        </header>
        <main>
          {route.name === "category" ? (
            <CategoryScreen
              key={route.category}
              category={route.category}
              month={route.month}
              account={route.account}
            />
          ) : (
            <>
              <div className="mb-6 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                <h1 className="text-title">{route.name === "settings" ? "Settings" : TABS.find((t) => t.id === tab)?.label}</h1>
                {route.name === "overview" && review > 0 && (
                  <a href="#review" className="text-sm font-semibold underline-offset-4 hover:underline">
                    {review} to review
                  </a>
                )}
                <BackupStatus />
              </div>
              {route.name === "transactions" ? (
                <Transactions />
              ) : route.name === "subscriptions" ? (
                <Subscriptions />
              ) : route.name === "assistant" ? (
                <Assistant />
              ) : route.name === "review" ? (
                <ReviewQueue />
              ) : route.name === "settings" ? (
                <Settings />
              ) : (
                <Overview account={route.account} />
              )}
            </>
          )}
        </main>
        <NavTabs
          tabs={TABS}
          tab={tab}
          className="glass fixed inset-x-2 bottom-3 z-20 flex justify-center gap-0 rounded-full p-1 sm:hidden"
          item="inline-flex min-h-11 items-center gap-2 rounded-full px-1.5 text-xs font-semibold [&_svg]:hidden"
        />
      </div>
    </>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <PrivacyProvider>
        <Shell />
      </PrivacyProvider>
    </QueryClientProvider>
  );
}
