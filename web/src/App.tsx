import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Inbox, MessageCircle, LayoutDashboard, ListOrdered, Repeat } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { useEffect, useRef } from "react";
import { Ambient } from "@/components/Ambient";
import { MotionFx } from "@/components/MotionFx";
import { BackupStatus } from "@/components/BackupStatus";
import { BottomBar } from "@/components/BottomBar";
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

// Screens keep their data across tab switches: a tab returns to what it showed, with no skeleton,
// and refreshes quietly in the background once the data is a minute old. Mutations still
// invalidate what they change.
export const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 60_000 } } });

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
  // after the first screen change the app is "settled": entrance animations (cards rising,
  // odometers rolling up) played once on load and do not replay on every tab switch
  const screen = route.name === "category" ? `category/${route.category}` : route.name;
  const first = useRef(screen);
  useEffect(() => {
    if (screen !== first.current) document.documentElement.dataset.settled = "";
  }, [screen]);
  return (
    <>
      <Ambient />
      <MotionFx />
      <div className="mx-auto w-full max-w-[1120px] px-4 pt-3 pb-[calc(6rem+env(safe-area-inset-bottom))] sm:px-9 sm:pb-16">
        <TopBar tabs={TABS} tab={tab} settings={route.name === "settings"} />
        <motion.main
          // a short fade and lift on every screen change; reduced motion keeps only the fade
          key={route.name === "category" ? `category/${route.category}` : route.name}
          // one quick crossfade between screens: no lift, nothing that reads as a reload
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: reduced ? 0.1 : 0.16, ease: [0.23, 1, 0.32, 1] }}
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
        <BottomBar tabs={TABS.filter((t) => t.id !== "subscriptions")} tab={tab} current={route.name} />
      </div>
    </>
  );
}

/** `children` render above the shell, inside its providers (the demo's banner). */
export function App({ children }: { children?: React.ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <PrivacyProvider>
        {children}
        <Shell />
      </PrivacyProvider>
    </QueryClientProvider>
  );
}
