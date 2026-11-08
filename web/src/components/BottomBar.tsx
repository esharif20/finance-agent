import {
  BookOpen,
  CalendarRange,
  Ellipsis,
  Newspaper,
  Repeat,
  Settings as SettingsIcon,
  Sparkles,
  Target,
} from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useId, useRef, useState } from "react";
import type { Tab } from "@/components/NavTabs";

const MORE = [
  { id: "subscriptions", label: "Subscriptions", icon: Repeat },
  { id: "goals", label: "Goals", icon: Target },
  { id: "plan", label: "Plan", icon: CalendarRange },
  { id: "briefing", label: "Briefing", icon: Newspaper },
  { id: "story", label: "Your month", icon: BookOpen },
  { id: "know", label: "How well I know you", icon: Sparkles },
  { id: "settings", label: "Settings", icon: SettingsIcon },
] as const;

const row =
  "flex min-h-12 items-center gap-3 rounded-[14px] px-3 text-[15px] font-semibold transition-[background-color,scale] duration-150 ease-out active:scale-[0.98] [@media(hover:hover)]:hover:bg-active";
const item =
  "relative flex min-h-12 flex-col items-center justify-center gap-0.5 rounded-[14px] pt-1 text-[11px] leading-tight font-semibold transition-[color,scale] duration-150 ease-out active:scale-[0.96]";

/** More: the less-used destinations, in a sheet above the bar. */
function More({ current }: { current: string }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const id = useId();
  const reduced = useReducedMotion();
  const on = MORE.some((m) => m.id === current);

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
    <div ref={box} className="contents">
      <button
        ref={button}
        type="button"
        aria-label="More"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((o) => !o)}
        className={`${item} ${on || open ? "text-foreground" : "text-muted-foreground"}`}
      >
        <Ellipsis className="size-[22px]" aria-hidden="true" />
        <span aria-hidden="true">More</span>
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            id={id}
            data-float
            initial={reduced ? { opacity: 0 } : { opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduced ? { opacity: 0 } : { opacity: 0, y: 12 }}
            transition={{ duration: reduced ? 0.1 : 0.2, ease: [0.23, 1, 0.32, 1] }}
            className="absolute inset-x-2 bottom-full mb-2 flex flex-col rounded-[22px] border border-[var(--card-edge)] bg-card p-1.5 shadow-[var(--glass-shadow)]"
          >
            {MORE.map(({ id: to, label, icon: Icon }) => (
              <a
                key={to}
                href={`#${to}`}
                aria-current={current === to ? "page" : undefined}
                onClick={() => setOpen(false)}
                className={`${row} ${current === to ? "bg-active" : ""}`}
              >
                <Icon className="size-[18px]" aria-hidden="true" />
                {label}
              </a>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/** Phones: an iOS-style tab bar docked to the bottom edge, above the home indicator. Four main
 * tabs with icon and label, the rest behind More. */
export function BottomBar({ tabs, tab, current }: { tabs: readonly Tab[]; tab: string; current: string }) {
  return (
    <nav
      aria-label="Main"
      className="bottom-bar glass fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 gap-1 px-2 pt-1.5 sm:hidden"
    >
      {tabs.map(({ id, label, icon, badge }) => (
        <a
          key={id}
          href={`#${id}`}
          aria-current={tab === id ? "page" : undefined}
          className={`${item} ${tab === id ? "text-foreground" : "text-muted-foreground"}`}
        >
          <span className="relative [&_svg]:size-[22px]">
            {icon}
            {!!badge && (
              <span
                aria-hidden="true"
                className="absolute -top-1.5 left-3.5 min-w-[18px] rounded-full bg-primary px-1 text-center text-[11px] leading-[18px] font-semibold text-primary-foreground"
              >
                {badge}
              </span>
            )}
          </span>
          <span className="max-w-full truncate">{label}</span>
          {!!badge && <span className="sr-only">, {badge} to review</span>}
        </a>
      ))}
      <More current={current} />
    </nav>
  );
}
