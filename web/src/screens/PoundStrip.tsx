import { motion, useReducedMotion } from "motion/react";
import { useId } from "react";
import { usePrivacy } from "@/lib/privacy";
import { poundStrip } from "@/lib/strip";

/** One bar that answers "where did my income go?": spent, then the hatched part kept. */
export function PoundStrip({ inMinor, outMinor }: { inMinor: number; outMinor: number }) {
  const reduce = useReducedMotion();
  const { hidden } = usePrivacy();
  const pattern = `hatch-${useId().replace(/:/g, "")}`;
  const strip = poundStrip(inMinor, outMinor);
  if (!strip) return null;

  const label = hidden
    ? "Amounts hidden."
    : strip.overspent
    ? "More went out than came in this month."
    : `Of every £1 that came in, ${strip.spentPence}p went out and ${strip.keptPence}p stayed.`;
  const grow = reduce
    ? {}
    : {
        initial: { scaleX: 0 },
        animate: { scaleX: 1 },
        transition: { duration: 0.22, ease: [0.2, 0.8, 0.2, 1] as const },
      };

  return (
    <div>
      <div
        role="img"
        aria-label={label}
        className="flex h-4 w-full gap-[3px] overflow-hidden rounded-full"
      >
        {strip.spentPence > 0 && (
          <motion.div
            {...grow}
            style={{ width: `${strip.spentPence}%`, originX: 0 }}
            className="rounded-full bg-spent"
          />
        )}
        {strip.keptPence > 0 && (
          <motion.div
            {...grow}
            style={{ width: `${strip.keptPence}%`, originX: 0 }}
            className="relative overflow-hidden rounded-full text-accent"
          >
            <svg className="absolute inset-0 size-full" aria-hidden="true">
              <defs>
                <pattern id={pattern} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                  <line x1="0" y1="0" x2="0" y2="6" stroke="currentColor" strokeWidth="3" />
                </pattern>
              </defs>
              <rect width="100%" height="100%" fill={`url(#${pattern})`} opacity="0.85" />
            </svg>
          </motion.div>
        )}
      </div>
      <p className="mt-3 text-sm text-muted-foreground">
        {strip.overspent ? (
          <span className="money">{label}</span>
        ) : (
          <>
            <span className="money font-semibold text-foreground">{strip.spentPence}p</span> of every £1 went out,{" "}
            <span className="money font-semibold text-foreground">{strip.keptPence}p</span> stayed.
          </>
        )}
      </p>
    </div>
  );
}
