import { useMutation } from "@tanstack/react-query";
import { motion, useReducedMotion } from "motion/react";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { simulatePurchase } from "@/api/client";
import { Money } from "@/components/Money";
import { parseAmount, riskCopy } from "@/lib/whatif";
import { cn } from "@/lib/utils";

// #153: "what if I buy this?" The API (#151) projects the balance; nothing is computed here.

const field =
  "min-h-11 w-full rounded-full border border-[var(--control)] bg-card px-4 text-sm font-medium aria-[invalid=true]:border-warning-foreground";
const btn =
  "inline-flex min-h-11 items-center justify-center rounded-full px-5 text-sm font-semibold transition-[background-color,transform] duration-150 ease-out active:scale-[0.97] disabled:opacity-50";

const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const longDate = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });

export function WhatIfDialog({ onClose }: { onClose: () => void }) {
  const reduce = useReducedMotion();
  const ids = { title: useId(), amount: useId(), date: useId(), buffer: useId(), err: useId() };
  const panel = useRef<HTMLFormElement>(null);
  const [amount, setAmount] = useState("");
  const [buffer, setBuffer] = useState("");
  const [on, setOn] = useState(today);
  const [problem, setProblem] = useState<string | null>(null);
  const check = useMutation({
    mutationFn: ({ a, b }: { a: number; b: number }) => simulatePurchase(a, on, b),
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

  const stale = () => {
    setProblem(null);
    check.reset();
  };
  const error = problem ?? (check.error ? check.error.message : null);
  const r = check.data;
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
          const a = parseAmount(amount);
          const b = buffer.trim() ? parseAmount(buffer) : 0;
          if (a === null) return setProblem("Enter the price in pounds, like 249.99.");
          if (b === null) return setProblem("Enter the buffer in pounds, like 100, or leave it empty.");
          setProblem(null);
          check.mutate({ a, b });
        }}
        className="flex w-full max-w-sm flex-col gap-4 rounded-card bg-background p-5 text-foreground shadow-[var(--glass-shadow)]"
      >
        <div>
          <h2 id={ids.title} className="text-heading">
            Check a purchase
          </h2>
          <p className="mt-1 text-label text-muted-foreground">
            See how low your balance could get before your next payday.
          </p>
        </div>
        <label htmlFor={ids.amount} className="flex flex-col gap-1.5 text-label">
          Price (£)
          <input
            id={ids.amount}
            inputMode="decimal"
            autoComplete="off"
            placeholder="249.99"
            value={amount}
            onChange={(e) => (setAmount(e.target.value), stale())}
            aria-invalid={!!error && parseAmount(amount) === null}
            aria-describedby={error ? ids.err : undefined}
            className={cn(field, "tabular-nums")}
          />
        </label>
        <label htmlFor={ids.date} className="flex flex-col gap-1.5 text-label">
          Date
          <input
            id={ids.date}
            type="date"
            required
            min={today()}
            value={on}
            onChange={(e) => (setOn(e.target.value), stale())}
            className={field}
          />
        </label>
        <label htmlFor={ids.buffer} className="flex flex-col gap-1.5 text-label">
          Keep at least (£, optional)
          <input
            id={ids.buffer}
            inputMode="decimal"
            autoComplete="off"
            placeholder="0"
            value={buffer}
            onChange={(e) => (setBuffer(e.target.value), stale())}
            className={cn(field, "tabular-nums")}
          />
        </label>
        {error && (
          <p id={ids.err} role="alert" className="text-label text-warning-foreground">
            {error}
          </p>
        )}
        {r?.status === "insufficient" && (
          <p role="status" className="rounded-card bg-active p-4 text-sm">
            Not enough history to project this yet: {r.have ?? 0} of {r.need ?? 0} pay cycles so far.
          </p>
        )}
        {r?.status === "ok" && r.minimum && r.on && r.risk && (
          <div role="status" className="rounded-card bg-active p-4">
            <p className="text-label text-muted-foreground">Lowest balance before payday</p>
            <p className="mt-0.5">
              <Money value={r.minimum} className="text-title tabular-nums" />
            </p>
            <p className="mt-1 text-label text-muted-foreground">
              on {longDate(r.on)}
              {r.floor && (
                <>
                  , possibly as low as <Money value={r.floor} />
                </>
              )}
            </p>
            <p
              className={cn(
                "mt-3 text-sm font-semibold",
                r.risk === "none" ? "text-positive" : "text-warning-foreground",
              )}
            >
              {riskCopy(r.risk)}
            </p>
          </div>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={`${btn} bg-active [@media(hover:hover)]:hover:bg-raised`}>
            Close
          </button>
          <button type="submit" disabled={check.isPending || !on} className={`${btn} bg-primary text-primary-foreground`}>
            Check
          </button>
        </div>
      </motion.form>
    </div>,
    document.body,
  );
}
