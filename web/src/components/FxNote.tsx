import { Info, TriangleAlert } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useId, useState } from "react";
import type { components } from "@/api/schema";
import { RateDialog } from "./RateDialog";

// #156: provenance and "rate needed" for the GBP totals; the API chose every rate.

type FxMonth = components["schemas"]["FxMonthOut"];
type Rate = components["schemas"]["RateOut"];

/** A Decimal string rounded half up to 4 places with BigInt: no float touches the rate. */
export function rateText(rate: string): string {
  const [whole = "0", frac = ""] = rate.split(".");
  const tenths = BigInt(whole + `${frac}00000`.slice(0, 5));
  const s = ((tenths + 5n) / 10n).toString().padStart(5, "0");
  return `${s.slice(0, -4)}.${s.slice(-4)}`;
}

const day = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });

export function rateSource(r: Rate): string {
  if (r.source === "bank") return `the bank's own GBP amount, ${day(r.on)}`;
  if (r.source === "exchange") return `your exchange on ${day(r.on)}`;
  return `your rate from ${day(r.on)}`;
}

const transactions = (n: number) => `${n} transaction${n === 1 ? "" : "s"}`;

/** Foreign payments left out of the month's figures for want of a rate. */
export function RateNeeded({ fx }: { fx?: FxMonth }) {
  const [entering, setEntering] = useState<string>();
  if (!fx?.rate_needed.length) return null;
  const which = fx.rate_needed.map((r) => `${r.count} ${r.currency} transaction${r.count === 1 ? "" : "s"}`);
  return (
    <div className="flex flex-col gap-2 rounded-[10px] bg-warning p-3 text-sm text-warning-foreground">
      <p role="status" className="flex items-start gap-2">
        <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        <span>
          <strong className="font-semibold">Rate needed</strong> for {which.join(" and ")}, so{" "}
          {fx.rate_needed.length === 1 && fx.rate_needed[0]!.count === 1 ? "it's" : "they're"} left out of these
          figures. Rates are never fetched or guessed.
        </span>
      </p>
      <div className="flex flex-wrap gap-2 pl-6">
        {fx.rate_needed.map((r) => (
          <button
            key={r.currency}
            type="button"
            aria-label={`Enter a rate for ${r.currency}`}
            onClick={() => setEntering(r.currency)}
            className="inline-flex min-h-11 items-center rounded-full border border-current px-4 font-semibold transition-transform duration-150 ease-out active:scale-[0.97]"
          >
            Enter a {r.currency} rate
          </button>
        ))}
      </div>
      {entering && <RateDialog currency={entering} month={fx.month} onClose={() => setEntering(undefined)} />}
    </div>
  );
}

/** A toggletip beside a total: which rate turned each foreign currency into pounds, and from where. */
export function FxProvenance({ fx }: { fx?: FxMonth }) {
  const [open, setOpen] = useState(false);
  const reduce = useReducedMotion();
  const id = useId();
  if (!fx?.rates.length) return null;
  return (
    <span className="relative inline-flex align-middle">
      <button
        type="button"
        aria-label="How foreign amounts were converted"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
        }}
        onBlur={() => setOpen(false)}
        className="-my-3 inline-flex size-11 items-center justify-center rounded-full text-muted-foreground transition-colors duration-150 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
      >
        <Info className="size-4" aria-hidden="true" />
      </button>
      <AnimatePresence>
        {open && (
          <motion.span
            id={id}
            role="note"
            initial={{ opacity: 0, scale: reduce ? 1 : 0.97 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, transition: { duration: 0.1 } }}
            transition={{ duration: 0.15, ease: [0.23, 1, 0.32, 1] }}
            style={{ transformOrigin: "top left" }}
            className="absolute top-full left-0 z-20 mt-1 block w-max max-w-72 rounded-[10px] bg-foreground p-3 text-sm text-background shadow-[var(--glass-shadow)]"
          >
            <span className="block font-semibold">Converted to pounds at</span>
            {fx.rates.map((r) => (
              <span key={`${r.currency}-${r.source}-${r.on}`} className="mt-1 block">
                {r.currency} at £{rateText(r.rate)}, from {rateSource(r)} ({transactions(r.count)})
              </span>
            ))}
          </motion.span>
        )}
      </AnimatePresence>
    </span>
  );
}
