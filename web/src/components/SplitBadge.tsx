import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import { type SplitOut, fetchSplit, revertSplit } from "@/api/client";
import { Money } from "@/components/Money";
import { NoteField } from "@/components/NoteField";
import { categoryHref } from "@/lib/route";

export const splitKey = (txn: number) => ["split", txn] as const;

/** The badge text: "Split · 3". */
export const splitLabel = (parts: number) => `Split · ${parts}`;

const pill = "inline-flex min-h-8 items-center gap-1 rounded-full bg-raised px-3 text-label font-semibold";

const useSplit = (txn: number) => useQuery({ queryKey: splitKey(txn), queryFn: () => fetchSplit(txn) });

/** "Split · N" on a Transaction that has a live Split; toggles the parts panel under the row. */
export function SplitBadge({ txn, open, controls, onToggle }: { txn: number; open: boolean; controls: string; onToggle: () => void }) {
  const s = useSplit(txn).data;
  if (!s) return null;
  return (
    <button
      type="button"
      aria-expanded={open}
      aria-controls={controls}
      onClick={onToggle}
      className={`${pill} transition-transform duration-150 ease-out active:scale-[0.97]`}
    >
      {splitLabel(s.parts.length)}
      {s.needs_recheck && <span className="rounded-full bg-warning px-2 text-meta text-warning-foreground">Re-check</span>}
      <ChevronDown className={`size-3.5 transition-transform duration-200 ease-out motion-reduce:transition-none ${open ? "rotate-180" : ""}`} aria-hidden="true" />
    </button>
  );
}

/** The parts of a Split, each with its own note, and the whole-Split undo. */
export function SplitParts({ txn, id, open, onClose, onResplit }: { txn: number; id: string; open: boolean; onClose: () => void; onResplit?: (s: SplitOut) => void }) {
  const client = useQueryClient();
  const reduce = useReducedMotion();
  const s = useSplit(txn).data;
  const undo = useMutation({
    mutationFn: () => revertSplit(txn),
    onSuccess: () => {
      onClose();
      return client.invalidateQueries();
    },
  });
  return (
    <AnimatePresence initial={false}>
      {open && s && (
        <motion.div
          id={id}
          initial={reduce ? { opacity: 0 } : { opacity: 0, y: -4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, transition: { duration: 0.1 } }}
          transition={{ duration: 0.18, ease: [0.2, 0.8, 0.2, 1] }}
          className="col-span-full order-5 mt-1 flex flex-col gap-3 rounded-[14px] bg-card p-4 sm:order-none"
        >
          {s.needs_recheck && (
            <p className="text-sm">
              The payment changed after you split it. It counts whole under its largest part until you re-split or undo.
            </p>
          )}
          <ol aria-label="Parts" className="flex flex-col gap-4">
            {s.parts.map((p, i) => (
              <li key={i} className="flex flex-col gap-2">
                <p className="flex items-baseline justify-between gap-3 text-row font-semibold">
                  <a href={categoryHref(p.category[0] ?? "")} className="min-w-0 truncate underline-offset-4 hover:underline">
                    {p.category[1]}
                  </a>
                  <Money value={{ minor: p.amount_minor, currency: s.currency }} sign />
                </p>
                <NoteField txn={txn} part={i} label={`Note on part ${i + 1}`} />
              </li>
            ))}
          </ol>
          <div className="flex flex-wrap gap-2">
            {onResplit && (
              <button type="button" onClick={() => onResplit(s)} className="min-h-11 rounded-full bg-active px-4 text-sm font-semibold transition-transform duration-150 ease-out active:scale-[0.97]">
                Re-split
              </button>
            )}
            <button type="button" disabled={undo.isPending} onClick={() => undo.mutate()} className="min-h-11 rounded-full bg-active px-4 text-sm font-semibold transition-transform duration-150 ease-out active:scale-[0.97] disabled:opacity-50">
              Undo split
            </button>
          </div>
          {undo.error && (
            <p role="alert" className="text-sm">
              {undo.error.message}
            </p>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
