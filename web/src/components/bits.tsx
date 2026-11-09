/* Small components adapted from React Bits (reactbits.dev, MIT, David Haz): SpotlightCard,
   AnimatedList's item, ShinyText and Magnet. Adapted to this app: transform and opacity are the
   only animated properties, nothing moves under reduced motion or on touch, and none of them
   touches a figure (Money keeps its own privacy blur). CountUp lives in lib/countup.ts. */
import { useReducedMotion } from "motion/react";
import { type ComponentProps, type CSSProperties, type PointerEvent, type ReactNode, useRef } from "react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const fine = () => typeof matchMedia === "function" && matchMedia("(hover: hover) and (pointer: fine)").matches;

/** SpotlightCard: a Card whose light follows the mouse, in `spotlightColor`. */
export function SpotlightCard({ spotlightColor, style, onPointerMove, ...props }: ComponentProps<typeof Card> & { spotlightColor?: string }) {
  const move = (e: PointerEvent<HTMLElement>) => {
    onPointerMove?.(e);
    if (e.pointerType !== "mouse") return;
    const r = e.currentTarget.getBoundingClientRect();
    e.currentTarget.style.setProperty("--mx", `${Math.round(e.clientX - r.left)}px`);
    e.currentTarget.style.setProperty("--my", `${Math.round(e.clientY - r.top)}px`);
  };
  return <Card {...props} onPointerMove={move} style={{ ...(spotlightColor ? { "--glow": spotlightColor } : {}), ...style } as CSSProperties} />;
}

/** AnimatedList's item, as props for a motion.li: each row rises in once as it first comes into
    view, a few dozen ms after the one before it (capped, so a long list never waits). */
export function useAnimatedItem(index = 0) {
  const reduce = useReducedMotion();
  if (reduce || typeof IntersectionObserver !== "function") return {}; // no observer: shown at once
  return {
    initial: { opacity: 0, y: 8, scale: 0.98 },
    whileInView: { opacity: 1, y: 0, scale: 1 },
    viewport: { once: true, margin: "0px 0px -4% 0px" },
    transition: { duration: 0.32, ease: [0.23, 1, 0.32, 1] as const, delay: Math.min(index, 6) * 0.04 },
  };
}

/** ShinyText: one pass of light across its content as it appears (index.css .fx-shine). */
export function ShinyText({ children, disabled, className }: { children: ReactNode; disabled?: boolean; className?: string }) {
  return <span className={cn("block", !disabled && "fx-shine", className)}>{children}</span>;
}

/** Magnet: the child leans toward the mouse while it is within `padding` px of it. */
export function Magnet({ children, padding = 24, strength = 4, className }: { children: ReactNode; padding?: number; strength?: number; className?: string }) {
  const reduce = useReducedMotion();
  const box = useRef<HTMLSpanElement>(null);
  const move = (e: PointerEvent<HTMLSpanElement>) => {
    const el = box.current;
    if (!el || reduce || e.pointerType !== "mouse" || !fine()) return;
    const r = el.getBoundingClientRect();
    const dx = e.clientX - (r.left + r.width / 2);
    const dy = e.clientY - (r.top + r.height / 2);
    const near = Math.abs(dx) < r.width / 2 + padding && Math.abs(dy) < r.height / 2 + padding;
    el.style.transform = near ? `translate3d(${(dx / strength / 4).toFixed(1)}px, ${(dy / strength / 4).toFixed(1)}px, 0)` : "";
  };
  return (
    <span
      ref={box}
      className={cn("inline-block transition-transform duration-300 ease-out motion-reduce:transition-none", className)}
      onPointerMove={move}
      onPointerLeave={() => box.current && (box.current.style.transform = "")}
    >
      {children}
    </span>
  );
}
