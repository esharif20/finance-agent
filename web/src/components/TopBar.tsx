import { Ellipsis, Eye, EyeOff, Settings as SettingsIcon } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { useEffect, useId, useRef, useState } from "react";
import { IconSwap } from "@/components/IconSwap";
import { NavTabs, type Tab } from "@/components/NavTabs";
import { iconButton, SyncNow } from "@/components/SyncNow";
import { usePrivacy } from "@/lib/privacy";

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

/** Eye toggle for privacy mode; `H` does the same anywhere (PrivacyProvider). */
function HideAmounts({ className = "" }: { className?: string }) {
  const { hidden, toggle } = usePrivacy();
  return (
    <button
      type="button"
      aria-label="Hide amounts"
      aria-pressed={hidden}
      aria-keyshortcuts="H"
      data-tip={`${hidden ? "Show" : "Hide"} amounts · H`}
      data-tip-align="end"
      onClick={toggle}
      className={`${iconButton} ${className}`}
    >
      {eye(hidden)}
    </button>
  );
}

const row =
  "flex min-h-11 items-center gap-3 rounded-[12px] px-3 text-sm font-semibold transition-colors [@media(hover:hover)]:hover:bg-active";

/** Phones: Settings and Hide amounts fold into one menu so the bar keeps its single row. */
function More({ settings }: { settings: boolean }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const id = useId();
  const reduced = useReducedMotion();
  const { hidden, toggle } = usePrivacy();

  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      button.current?.focus();
    };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  return (
    <div ref={box} className="relative sm:hidden">
      <button
        ref={button}
        type="button"
        aria-label="More"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((o) => !o)}
        className={`${iconButton} ${open ? "bg-active" : ""}`}
      >
        <Ellipsis className="size-[18px]" aria-hidden="true" />
      </button>
      {open && (
        <motion.div
          id={id}
          data-float
          initial={reduced ? { opacity: 0 } : { opacity: 0, scale: 0.96, y: -4 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          transition={{ duration: reduced ? 0.1 : 0.16, ease: [0.23, 1, 0.32, 1] }}
          style={{ transformOrigin: "top right" }}
          className="absolute top-full right-0 mt-3 flex w-52 flex-col rounded-[20px] border border-[var(--card-edge)] bg-card p-1.5 shadow-[var(--glass-shadow)]"
        >
          <button type="button" aria-pressed={hidden} onClick={toggle} className={row}>
            {eye(hidden)}
            Hide amounts
          </button>
          <a
            href="#settings"
            aria-current={settings ? "page" : undefined}
            onClick={() => setOpen(false)}
            className={`${row} ${settings ? "bg-active" : ""}`}
          >
            <SettingsIcon className="size-[18px]" aria-hidden="true" />
            Settings
          </a>
        </motion.div>
      )}
    </div>
  );
}

/**
 * The app's one-row top bar (#214). Nothing in it ever wraps; it gives up text as width runs out:
 * phones keep the wordmark and fold Settings and Hide amounts into More (tabs live in the bottom
 * bar); from 640px the tabs join as icons with tooltips; from 1024px they are words; from 1280px
 * words with icons.
 */
export function TopBar({ tabs, tab, settings }: { tabs: readonly Tab[]; tab: string; settings: boolean }) {
  return (
    <header className="glass sticky top-3 z-20 mb-6 flex items-center gap-1 rounded-full p-1.5 pl-3 whitespace-nowrap">
      <a
        href="#overview"
        className="mr-1 flex shrink-0 items-center gap-2.5 rounded-full pr-2 transition-[scale] duration-150 ease-out active:scale-[0.97]"
      >
        <Mark />
        <span className="text-base font-semibold sm:max-md:sr-only">Finance Agent</span>
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
        <SyncNow />
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
        <HideAmounts className="max-sm:hidden" />
        <More settings={settings} />
      </div>
    </header>
  );
}
