import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Eye, EyeOff, LayoutDashboard } from "lucide-react";
import { PrivacyProvider, usePrivacy } from "@/lib/privacy";
import { useRoute } from "@/lib/route";
import { CategoryScreen } from "@/screens/CategoryScreen";
import { Overview } from "@/screens/Overview";

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
  const Icon = hidden ? EyeOff : Eye;
  return (
    <button
      type="button"
      aria-pressed={hidden}
      onClick={toggle}
      className="ml-auto inline-flex min-h-11 items-center gap-2 rounded-full glass px-4 text-sm font-semibold transition-colors hover:bg-active"
    >
      <Icon className="size-4" aria-hidden="true" />
      Hide amounts
      <kbd className="hidden rounded-md bg-raised px-1.5 text-xs text-muted-foreground sm:inline">H</kbd>
    </button>
  );
}

export function App() {
  const route = useRoute();
  return (
    <QueryClientProvider client={queryClient}>
      <PrivacyProvider>
      <div className="mx-auto w-full max-w-[1120px] px-4 pt-3 pb-28 sm:px-9 sm:pb-16">
        <header className="glass sticky top-3 z-20 mb-6 flex items-center gap-3 rounded-full py-2 pr-2 pl-3">
          <Mark />
          <span className="text-base font-semibold">Finance Agent</span>
          <HideAmounts />
        </header>
        <main>
          {route.name === "category" ? (
            <CategoryScreen key={route.category} category={route.category} month={route.month} />
          ) : (
            <>
              <h1 className="mb-6 text-[28px] leading-9 font-semibold tracking-[-0.01em]">Overview</h1>
              <Overview />
            </>
          )}
        </main>
        <nav
          aria-label="Main"
          className="glass fixed inset-x-4 bottom-3 z-20 flex justify-center rounded-full p-1.5 sm:hidden"
        >
          <a
            href="#overview"
            aria-current={route.name === "overview" ? "page" : undefined}
            className="inline-flex min-h-11 items-center gap-2 rounded-full bg-foreground/10 px-5 text-sm font-semibold"
          >
            <LayoutDashboard className="size-4" aria-hidden="true" />
            Overview
          </a>
        </nav>
      </div>
      </PrivacyProvider>
    </QueryClientProvider>
  );
}
