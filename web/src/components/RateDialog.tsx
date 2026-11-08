import { useMutation, useQueryClient } from "@tanstack/react-query";
import { motion, useReducedMotion } from "motion/react";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { putRate } from "@/api/fxRate";
import { checkRate } from "@/lib/rate";

// #187: enter a rate for one currency and date. The API is the authority (#181); checkRate only
// refuses what it would refuse, before it is sent.

const field =
  "min-h-11 w-full rounded-full border border-[var(--control)] bg-card px-4 text-sm font-medium aria-[invalid=true]:border-warning-foreground";
const btn =
  "inline-flex min-h-11 items-center justify-center rounded-full px-5 text-sm font-semibold transition-[background-color,transform] duration-150 ease-out active:scale-[0.97] disabled:opacity-50";

export function RateDialog({ currency, month, onClose }: { currency: string; month: string; onClose: () => void }) {
  const client = useQueryClient();
  const reduce = useReducedMotion();
  const ids = { title: useId(), rate: useId(), date: useId(), hint: useId(), err: useId() };
  const panel = useRef<HTMLFormElement>(null);
  const [rate, setRate] = useState("");
  const [on, setOn] = useState(`${month}-01`); // the latest rate on or before a payment's date applies, so the 1st covers the month
  const [problem, setProblem] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => putRate(currency, on, rate.trim()),
    // every total, balance and cost reads rates on the server: refetch them all
    onSuccess: () => {
      onClose();
      void client.invalidateQueries();
    },
  });

  // focus goes in on open and back to the opener on close
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    panel.current?.querySelector<HTMLElement>("input")?.focus();
    return () => opener?.focus();
  }, []);

  const trap = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") return onClose();
    if (e.key !== "Tab") return;
    const items = [...(panel.current?.querySelectorAll<HTMLElement>("input, button:not(:disabled)") ?? [])];
    const first = items[0];
    const last = items.at(-1);
    if (e.shiftKey && document.activeElement === first) (e.preventDefault(), last?.focus());
    else if (!e.shiftKey && document.activeElement === last) (e.preventDefault(), first?.focus());
  };

  const error = problem ?? (save.error ? save.error.message : null);
  // portalled: the glass card's backdrop filter would otherwise be this overlay's containing block
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-4 sm:items-center" onMouseDown={onClose}>
      <motion.form
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={ids.title}
        noValidate
        initial={{ opacity: 0, scale: reduce ? 1 : 0.97 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.18, ease: [0.23, 1, 0.32, 1] }}
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={trap}
        onSubmit={(e) => {
          e.preventDefault();
          const p = checkRate(rate);
          setProblem(p);
          if (!p) save.mutate();
        }}
        className="flex w-full max-w-sm flex-col gap-4 rounded-card bg-background p-5 text-foreground shadow-[var(--glass-shadow)]"
      >
        <div>
          <h2 id={ids.title} className="text-heading">
            Enter a {currency} rate
          </h2>
          <p id={ids.hint} className="mt-1 text-label text-muted-foreground">
            Pounds for one {currency}. It applies to payments from this date on, until a newer rate. It is kept on this
            computer and never fetched.
          </p>
        </div>
        <label htmlFor={ids.rate} className="flex flex-col gap-1.5 text-label">
          Rate (£ per 1 {currency})
          <input
            id={ids.rate}
            inputMode="decimal"
            autoComplete="off"
            spellCheck={false}
            placeholder="0.79"
            value={rate}
            onChange={(e) => {
              setRate(e.target.value);
              setProblem(null);
              save.reset();
            }}
            aria-invalid={!!error}
            aria-describedby={`${ids.hint} ${error ? ids.err : ""}`}
            className={`${field} tabular-nums`}
          />
        </label>
        <label htmlFor={ids.date} className="flex flex-col gap-1.5 text-label">
          Date
          <input
            id={ids.date}
            type="date"
            required
            value={on}
            onChange={(e) => setOn(e.target.value)}
            className={field}
          />
        </label>
        {error && (
          <p id={ids.err} role="alert" className="text-label text-warning-foreground">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={`${btn} bg-active [@media(hover:hover)]:hover:bg-raised`}>
            Cancel
          </button>
          <button type="submit" disabled={save.isPending || !on} className={`${btn} bg-primary text-primary-foreground`}>
            Save rate
          </button>
        </div>
      </motion.form>
    </div>,
    document.body,
  );
}
