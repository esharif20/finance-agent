import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Eye, EyeOff, LayoutDashboard, ListOrdered } from "lucide-react";
import { Ambient } from "@/components/Ambient";
import { PrivacyProvider, usePrivacy } from "@/lib/privacy";
import { useRoute } from "@/lib/route";
import { CategoryScreen } from "@/screens/CategoryScreen";
import { Overview } from "@/screens/Overview";
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

const TABS = [
  { id: "overview", label: "Overview", Icon: LayoutDashboard },
  { id: "transactions", label: "Transactions", Icon: ListOrdered },
] as const;

function Tabs({ tab, className, item }: { tab: string; className: string; item: string }) {
  return (
    <nav aria-label="Main" className={className}>
      {TABS.map(({ id, label, Icon }) => (
        <a
          key={id}
          href={`#${id}`}
          aria-current={tab === id ? "page" : undefined}
          className={`${item} ${tab === id ? "bg-foreground/10" : "hover:bg-active"}`}
        >
          <Icon className="size-4" aria-hidden="true" />
          {label}
        </a>
      ))}
    </nav>
  );
}

export function App() {
  const route = useRoute();
  const tab = route.name === "transactions" ? "transactions" : "overview";
  return (
    <QueryClientProvider client={queryClient}>
      <PrivacyProvider>
      <Ambient />
      <div className="mx-auto w-full max-w-[1120px] px-4 pt-3 pb-28 sm:px-9 sm:pb-16">
        <header className="glass sticky top-3 z-20 mb-6 flex items-center gap-3 rounded-full py-2 pr-2 pl-3">
          <Mark />
          <span className="text-base font-semibold">Finance Agent</span>
          <Tabs
            tab={tab}
            className="ml-4 hidden gap-1 sm:flex"
            item="inline-flex min-h-11 items-center gap-2 rounded-full px-4 text-sm font-semibold"
          />
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
              <h1 className="mb-6 text-title">
                {TABS.find((t) => t.id === tab)?.label}
              </h1>
              {route.name === "transactions" ? <Transactions /> : <Overview account={route.account} />}
            </>
          )}
        </main>
        <Tabs
          tab={tab}
          className="glass fixed inset-x-4 bottom-3 z-20 flex justify-center gap-1 rounded-full p-1.5 sm:hidden"
          item="inline-flex min-h-11 items-center gap-2 rounded-full px-5 text-sm font-semibold"
        />
      </div>
      </PrivacyProvider>
    </QueryClientProvider>
  );
}
