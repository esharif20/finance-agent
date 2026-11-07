import { ChevronDown } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { CategoryGroup } from "@/api/client";
import { cn } from "@/lib/utils";

export type Category = [string, string];

/** One hue per top-level Category, in taxonomy order; "Uncategorised" is neutral. */
export function hueFor(top: string, groups: CategoryGroup[]): string {
  if (top === "To review") return "var(--cat-review)";
  const i = groups.findIndex((g) => g.name === top);
  return top === "Uncategorised" || i < 0 ? "var(--cat-other)" : `var(--cat-${i % 10})`;
}

export function chipStyle(hue: string): { background: string; color: string } {
  return {
    background: `color-mix(in srgb, ${hue} var(--chip-tint), var(--card))`,
    color: `color-mix(in srgb, ${hue}, var(--chip-ink-mix))`,
  };
}

const label = (c: Category) => (c[0] === c[1] ? c[0] : c[1]);

const POPOVER_WIDTH = 288; // w-72
const EDGE_GAP = 8; // keep this much clear of the card's (or the window's) edge

/**
 * The Category of a Transaction, and its editor: click for a popover of every
 * Category; pick one to save a Correction (for this Transaction, or for every
 * Transaction of its Merchant).
 */
export function CategoryChip({
  value,
  groups,
  merchant,
  onChange,
}: {
  value: Category | null;
  groups: CategoryGroup[];
  merchant: string | null;
  onChange: (category: Category, allOfMerchant: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [all, setAll] = useState(false);
  const root = useRef<HTMLSpanElement>(null);
  const reduce = useReducedMotion();
  const id = useId();
  const [align, setAlign] = useState<"start" | "end">("start");

  // open towards the left when the popover would hang past the card's right edge
  useLayoutEffect(() => {
    if (!open || !root.current) return;
    const chip = root.current.getBoundingClientRect();
    const card = root.current.closest("[data-slot=card]")?.getBoundingClientRect();
    const edge = Math.min(card?.right ?? Infinity, window.innerWidth) - EDGE_GAP;
    setAlign(chip.left + POPOVER_WIDTH > edge ? "end" : "start");
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onDown = (e: MouseEvent) => !root.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  const hue = hueFor(value?.[0] ?? "Uncategorised", groups);
  return (
    <span ref={root} className="relative inline-block">
      <motion.button
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-label={`Category: ${value ? `${value[0]}, ${label(value)}` : "none"}. Change`}
        onClick={() => setOpen((o) => !o)}
        animate={chipStyle(hue)}
        transition={{ duration: reduce ? 0.1 : 0.2 }}
        className="inline-flex min-h-8 items-center gap-1 rounded-full px-3 text-label font-semibold"
      >
        {value ? label(value) : "Uncategorised"}
        <ChevronDown className="size-3.5" aria-hidden="true" />
      </motion.button>
      <AnimatePresence>
        {open && (
          <motion.div
            id={id}
            role="dialog"
            aria-label="Change category"
            initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, transition: { duration: 0.1 } }}
            transition={{ duration: 0.16, ease: [0.2, 0.8, 0.2, 1] }}
            data-align={align}
            style={{ transformOrigin: align === "end" ? "top right" : "top left" }}
            className={cn(
              "absolute top-full z-30",
              align === "end" ? "right-0" : "left-0",
              "mt-2 flex max-h-80 w-72 flex-col overflow-hidden rounded-[16px] border bg-card shadow-lg",
            )}
          >
            <div className="overflow-y-auto p-3">
              {groups.map((g) => (
                <div key={g.name} role="group" aria-label={g.name} className="mb-3 last:mb-0">
                  <p className="mb-1.5 text-meta text-muted-foreground">{g.name}</p>
                  <div className="flex flex-wrap gap-1.5">
                    {g.subs.map((sub) => {
                      const current = value?.[0] === g.name && value[1] === sub;
                      return (
                        <button
                          key={sub}
                          type="button"
                          aria-pressed={current}
                          onClick={() => {
                            setOpen(false);
                            if (!current || all) onChange([g.name, sub], all);
                          }}
                          style={chipStyle(hueFor(g.name, groups))}
                          className={cn(
                            "min-h-8 rounded-full px-3 text-label font-semibold",
                            current && "ring-2 ring-current",
                          )}
                        >
                          {sub}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
            {merchant && (
              <label className="flex min-h-11 items-center gap-2 border-t bg-raised px-3 text-sm">
                <input
                  type="checkbox"
                  checked={all}
                  onChange={(e) => setAll(e.target.checked)}
                  className="size-4 accent-[var(--accent)]"
                />
                Apply to all {merchant}
              </label>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </span>
  );
}
