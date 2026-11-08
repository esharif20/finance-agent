import { Eye, EyeOff, Settings as SettingsIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { IconSwap } from "@/components/IconSwap";
import { NavTabs, type Tab } from "@/components/NavTabs";
import { SearchBox } from "@/components/SearchBox";
import { iconButton, SyncNow } from "@/components/SyncNow";
import { usePrivacy } from "@/lib/privacy";

// the public demo has no bank to sync
const DEMO = import.meta.env.VITE_DEMO === "1";

function Mark() {
  // three bars of decreasing length: money being sorted
  return (
    <svg viewBox="0 0 32 32" className="size-8 shrink-0" aria-hidden="true">
      <rect width="32" height="32" rx="9" className="fill-primary" />
      <rect x="7" y="8" width="18" height="4" rx="2" className="fill-primary-foreground" />
      <rect x="7" y="14" width="13" height="4" rx="2" className="fill-primary-foreground" />
      <rect x="7" y="20" width="8" height="4" rx="2" className="fill-primary-foreground" />
    </svg>
  );
}

const eye = (hidden: boolean) => (
  <IconSwap on={hidden} a={<Eye className="size-[18px]" />} b={<EyeOff className="size-[18px]" />} />
);

/** Privacy mode, labelled so it is easy to find: blurs or shows every amount. `H` does the same
    anywhere (PrivacyProvider). The words say what a press does, so it needs no aria-pressed. */
function HideAmounts() {
  const { hidden, toggle } = usePrivacy();
  const verb = hidden ? "Show" : "Hide";
  return (
    <button
      type="button"
      // "values" shows from 640px; the name is fixed here and always starts with what shows
      aria-label={`${verb} values`}
      aria-keyshortcuts="H"
      data-tip={`${verb} values · H`}
      data-tip-align="end"
      onClick={toggle}
      className="inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center gap-1.5 rounded-full px-3 text-sm font-semibold transition-[background-color,scale] duration-150 ease-out active:scale-[0.96] [@media(hover:hover)]:hover:bg-active"
    >
      {eye(hidden)}
      <span aria-hidden="true">
        {verb}
        <span className="max-sm:hidden"> values</span>
      </span>
    </button>
  );
}

/** Phones: the bar slides away while you scroll down and comes back on the way up. */
function useHideOnScroll() {
  const [hidden, setHidden] = useState(false);
  useEffect(() => {
    let last = window.scrollY;
    const scroll = () => {
      const y = window.scrollY;
      if (Math.abs(y - last) < 8) return; // ignore jitter and the iOS bounce
      setHidden(y > last && y > 80);
      last = y;
    };
    window.addEventListener("scroll", scroll, { passive: true });
    return () => window.removeEventListener("scroll", scroll);
  }, []);
  return hidden;
}

/**
 * The app's one-row top bar (#214). Nothing in it ever wraps; it gives up text as width runs out:
 * phones keep the wordmark and search (tabs, Settings and Hide amounts live in the bottom bar);
 * from 640px the tabs join as icons with tooltips; from 1024px they are words; from 1280px
 * words with icons.
 */
export function TopBar({ tabs, tab, settings }: { tabs: readonly Tab[]; tab: string; settings: boolean }) {
  const away = useHideOnScroll();
  // the demo has its landing page at `/`; locally `/` is the app, so the mark goes to Overview
  const demo = import.meta.env.VITE_DEMO === "1";
  return (
    <header
      data-away={away}
      className="glass sticky top-3 z-20 mb-6 flex items-center gap-1 rounded-full p-1.5 pl-3 whitespace-nowrap transition-transform duration-300 ease-[cubic-bezier(0.23,1,0.32,1)] motion-reduce:transition-none max-sm:data-[away=true]:-translate-y-[calc(100%+1rem)]"
    >
      <a
        href={demo ? "/" : "#overview"}
        aria-label={demo ? "Finance Agent home" : undefined}
        className="mr-1 flex shrink-0 items-center gap-2.5 rounded-full pr-2 transition-[scale,opacity] duration-150 ease-out active:scale-[0.97] [@media(hover:hover)]:hover:opacity-80"
      >
        <Mark />
        <span className="text-base font-semibold max-[419px]:sr-only sm:max-md:sr-only">Finance Agent</span>
      </a>
      <NavTabs
        tabs={tabs}
        tab={tab}
        tip
        className="hidden min-w-0 gap-0.5 sm:flex"
        item="inline-flex min-h-11 min-w-11 items-center justify-center rounded-full px-3 text-sm font-semibold transition-[background-color,scale] duration-150 ease-out active:scale-[0.96] lg:px-4 lg:after:hidden lg:max-xl:[&_svg]:hidden"
        label="max-lg:sr-only"
      />
      <div className="ml-auto flex shrink-0 items-center gap-0.5">
        <SearchBox />
        {!DEMO && <SyncNow />}
        <a
          href="#settings"
          aria-label="Settings"
          aria-current={settings ? "page" : undefined}
          data-tip="Settings"
          data-tip-align="end"
          className={`${iconButton} max-sm:hidden ${settings ? "bg-foreground/10" : ""}`}
        >
          <SettingsIcon className="size-[18px]" aria-hidden="true" />
        </a>
        <HideAmounts />
      </div>
    </header>
  );
}
