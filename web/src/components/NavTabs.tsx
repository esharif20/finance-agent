import { motion, useReducedMotion } from "motion/react";
import { useId, type ReactNode } from "react";
import { onHashLink } from "@/lib/route";

export type Tab = { id: string; label: string; icon?: ReactNode; badge?: number };

/** Nav links with one marker that slides to the active link. layoutId only animates when the
 * marker moves, so nothing flies in on first render; reduced motion makes it jump. */
export function NavTabs({
  tabs,
  tab,
  className,
  item,
  label: labelClass,
  tip,
}: {
  tabs: readonly Tab[];
  tab: string;
  className: string;
  item: string;
  /** classes on each label, e.g. to hide it visually (it stays the link's name) when space runs out */
  label?: string;
  /** show the label as a tooltip too, for when it is visually hidden */
  tip?: boolean;
}) {
  const marker = useId(); // the desktop and mobile navs each keep their own marker
  const reduced = useReducedMotion();
  return (
    <nav aria-label="Main" className={className}>
      {tabs.map(({ id, label, icon, badge }) => (
        <a
          key={id}
          href={`#${id}`}
          onClick={onHashLink}
          aria-current={tab === id ? "page" : undefined}
          data-tip={tip ? label : undefined}
          className={`${item} relative ${tab === id ? "" : "[@media(hover:hover)]:hover:bg-active"}`}
        >
          {tab === id && (
            <motion.span
              layoutId={marker}
              data-marker
              data-reduced={reduced ? "true" : undefined}
              aria-hidden="true"
              className="absolute inset-0 rounded-full bg-foreground/10"
              transition={reduced ? { duration: 0 } : { duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
            />
          )}
          <span className="relative inline-flex items-center gap-2">
            {icon}
            <span className={labelClass}>{label}</span>
            {!!badge && (
              <>
                <span
                  aria-hidden="true"
                  className="min-w-5 rounded-full bg-primary px-1.5 text-center text-xs font-semibold text-primary-foreground"
                >
                  {badge}
                </span>
                <span className="sr-only">, {badge} to review</span>
              </>
            )}
          </span>
        </a>
      ))}
    </nav>
  );
}
