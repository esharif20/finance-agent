import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Inbox, MessageCircle, LayoutDashboard, ListOrdered, Repeat } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { Ambient } from "@/components/Ambient";
import { BackupStatus } from "@/components/BackupStatus";
import { NavTabs } from "@/components/NavTabs";
import { TopBar } from "@/components/TopBar";
import { PrivacyProvider } from "@/lib/privacy";
import { useRoute } from "@/lib/route";
import { Assistant } from "@/screens/Assistant";
import { CategoryScreen } from "@/screens/CategoryScreen";
import { Briefing } from "@/screens/Briefing";
import { Goals } from "@/screens/Goals";
import { KnowYou } from "@/screens/KnowYou";
import { Plan } from "@/screens/Plan";
import { Overview } from "@/screens/Overview";
import { ReviewQueue, useReviewCount } from "@/screens/ReviewQueue";
import { Settings } from "@/screens/Settings";
import { Story } from "@/screens/Story";
import { Subscriptions } from "@/screens/Subscriptions";
import { Transactions } from "@/screens/Transactions";

const queryClient = new QueryClient();

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
  const reduced = useReducedMotion();
  const tab = route.name === "transactions" || route.name === "review" || route.name === "subscriptions" || route.name === "assistant" ? route.name : route.name === "settings" || route.name === "briefing" || route.name === "know" || route.name === "goals" || route.name === "plan" || route.name === "story" ? "" : "overview";
  return (
    <>
      <Ambient />
      <div className="mx-auto w-full max-w-[1120px] px-4 pt-3 pb-28 sm:px-9 sm:pb-16">
        <TopBar tabs={TABS} tab={tab} settings={route.name === "settings"} />
        <motion.main
          // a short fade and lift on every screen change; reduced motion keeps only the fade
          key={route.name === "category" ? `category/${route.category}` : route.name}
          initial={reduced ? { opacity: 0 } : { opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: reduced ? 0.12 : 0.24, ease: [0.23, 1, 0.32, 1] }}
        >
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
                <h1 className="text-title">{route.name === "settings" ? "Settings" : route.name === "briefing" ? "Briefing" : route.name === "know" ? "How well I know you" : route.name === "goals" ? "Goals" : route.name === "plan" ? "Plan" : route.name === "story" ? "Your month" : TABS.find((t) => t.id === tab)?.label}</h1>
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
              ) : route.name === "briefing" ? (
                <Briefing />
              ) : route.name === "goals" ? (
                <Goals />
              ) : route.name === "plan" ? (
                <Plan />
              ) : route.name === "story" ? (
                <Story month={route.month} />
              ) : route.name === "know" ? (
                <KnowYou />
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
        </motion.main>
        <NavTabs
          tabs={TABS}
          tab={tab}
          // phones: icons, and the current tab alone spells out its name (5 labels never fit at 360px)
          className="glass fixed inset-x-2 bottom-3 z-20 flex justify-between rounded-full p-1 sm:hidden"
          item="inline-flex min-h-11 min-w-12 items-center justify-center rounded-full px-3 text-sm font-semibold transition-[background-color,scale] duration-150 ease-out active:scale-[0.94]"
          label="sr-only [[aria-current=page]_&]:not-sr-only"
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
