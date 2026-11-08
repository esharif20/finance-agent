// Shared motion pieces for the landing page: one reduced-motion read, the magnetic pill,
// the letter roll on links and the masked line rise. Hover moves only run for a fine pointer
// that can hover; keyboard focus gets the same end state through :focus-visible in CSS.
import { motion, useMotionValue, useSpring } from "motion/react";
import { type CSSProperties, Fragment, type PointerEvent, type ReactNode, useState } from "react";

/** Read once per mount, so a test (or a visitor) can switch it between renders. */
export function prefersReducedMotion() {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function finePointer() {
  return typeof window.matchMedia === "function" && window.matchMedia("(hover: hover) and (pointer: fine)").matches;
}

export const useStill = () => useState(prefersReducedMotion)[0];

/** Letters that roll up to a copy of themselves on hover or focus (CSS only). */
export function Roll({ text }: { text: string }) {
  return (
    <span className="lp-roll">
      <span className="lp-sr">{text}</span>
      <span aria-hidden="true" className="lp-roll-in">
        {[...text].map((ch, i) => (
          // capped so a long label still finishes in about 250ms
          <span key={i} data-c={ch === " " ? " " : ch} style={{ "--i": Math.min(i, 16) } as CSSProperties}>
            {ch === " " ? " " : ch}
          </span>
        ))}
      </span>
    </span>
  );
}

/** A pill link that leans toward the pointer and springs back (Codrops "Magnetic Buttons"). */
export function Magnetic({ href, className, children }: { href: string; className: string; children: ReactNode }) {
  const [live] = useState(() => finePointer() && !prefersReducedMotion());
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const sx = useSpring(x, { stiffness: 220, damping: 16, mass: 0.5 });
  const sy = useSpring(y, { stiffness: 220, damping: 16, mass: 0.5 });
  const move = (e: PointerEvent<HTMLAnchorElement>) => {
    if (!live || e.pointerType !== "mouse") return;
    const r = e.currentTarget.getBoundingClientRect();
    x.set((e.clientX - r.left - r.width / 2) * 0.32);
    y.set((e.clientY - r.top - r.height / 2) * 0.42);
  };
  const leave = () => {
    x.set(0);
    y.set(0);
  };
  return (
    <motion.a
      href={href}
      className={className}
      onPointerMove={move}
      onPointerLeave={leave}
      style={live ? { x: sx, y: sy } : undefined}
    >
      {children}
    </motion.a>
  );
}

const EXPO = [0.16, 1, 0.3, 1] as const;

/** Each authored line rises from behind its own baseline, once, as it enters (taste motion.md). */
export function Lines({
  lines,
  still,
  label,
  delay = 0,
  onLoad = false,
}: { lines: string[]; still: boolean; label?: string; delay?: number; onLoad?: boolean }) {
  if (still)
    return (
      <>
        {lines.map((l, i) => (
          <Fragment key={i}>
            {i > 0 && " "}
            <span className="lp-line">{l}</span>
          </Fragment>
        ))}
      </>
    );
  const shown = { y: "0%" };
  return (
    <>
      <span className="lp-sr">{label ?? lines.join(" ")}</span>
      {lines.map((l, i) => (
        <span key={i} className="lp-line lp-line-mask" aria-hidden="true">
          <motion.span
            className="lp-line-in"
            initial={{ y: "108%" }}
            {...(onLoad ? { animate: shown } : { whileInView: shown, viewport: { once: true, margin: "0px 0px -12% 0px" } })}
            transition={{ duration: 0.9, ease: EXPO, delay: delay + i * 0.11 }}
          >
            {l}
          </motion.span>
        </span>
      ))}
    </>
  );
}

/** A block that rises 24px into place once, as it enters. Never hides content without motion. */
export function Rise({ still, delay = 0, className, children }: { still: boolean; delay?: number; className?: string; children: ReactNode }) {
  if (still) return <div className={className}>{children}</div>;
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 24 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "0px 0px -10% 0px" }}
      transition={{ duration: 0.8, ease: EXPO, delay }}
    >
      {children}
    </motion.div>
  );
}
