import { useReducedMotion } from "motion/react";
import { useEffect, useState } from "react";
import { useCountUp } from "@/lib/countup";
import { type Money as MoneyValue, formatMoney, formatMoneyParts } from "@/lib/money";
import { usePrivacy } from "@/lib/privacy";
import { cn } from "@/lib/utils";

// Keys whose odometer has played this session: it runs on first load only, never on refetch.
const played = new Set<string>();

/** Digits as columns of a 0-9 reel; CSS rolls each from 0 and rests on its real digit (--d). */
function Reel({ text }: { text: string }) {
  let i = 0;
  return (
    <>
      {[...text].map((ch, k) =>
        /\d/.test(ch) ? (
          <span key={k} className="odo-col" style={{ "--d": ch, "--i": i++ } as React.CSSProperties}>
            <span className="odo-reel">
              {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => (
                <span key={n}>{n}</span>
              ))}
            </span>
          </span>
        ) : (
          ch
        ),
      )}
    </>
  );
}

/**
 * Every amount on screen goes through here. Pence are set a step smaller.
 * In privacy mode the CSS blurs `.money` and screen readers hear "Amount hidden".
 */
export function Money({
  value,
  sign,
  countUp,
  odometer,
  className,
}: {
  value: MoneyValue;
  sign?: boolean;
  countUp?: boolean;
  /** Roll the digits once per session, keyed by this string. Never under Hide amounts or reduced motion. */
  odometer?: string;
  className?: string;
}) {
  const { hidden } = usePrivacy();
  const reduce = useReducedMotion();
  // Decided on first render only, so a refetch never restarts or cancels the roll.
  const [roll] = useState(() => odometer !== undefined && !played.has(odometer));
  useEffect(() => {
    if (odometer !== undefined) played.add(odometer);
  }, [odometer]);
  const reel = roll && !hidden && !reduce;
  const minor = useCountUp(value.minor, countUp === true);
  const { main, fraction } = formatMoneyParts({ ...value, minor }, { sign });
  return (
    <span className={cn("money whitespace-nowrap", className)}>
      <span className="sr-only">{hidden ? "Amount hidden" : formatMoney(value, { sign })}</span>
      <span aria-hidden="true" className={reel ? "odometer" : undefined} data-play={reel ? "" : undefined}>
        {reel ? <Reel text={main} /> : main}
        <span className="money-minor">{reel ? <Reel text={fraction} /> : fraction}</span>
      </span>
    </span>
  );
}
