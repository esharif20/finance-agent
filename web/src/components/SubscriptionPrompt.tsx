import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useState } from "react";
import { confirmSubscription, dismissSubscription } from "@/api/client";
import { Card } from "@/components/ui/card";
import { type Entry as SubscriptionEntry, type Kind, reasonText } from "@/lib/subscriptions";
import { PlacementSelect, type Place } from "./PlacementSelect";

export const SUBS_KEY = ["subscriptions"] as const;
const btn =
  "inline-flex min-h-11 items-center justify-center rounded-full px-5 text-sm font-semibold transition-[background-color,transform] duration-150 ease-out active:scale-[0.97] disabled:opacity-50";

const placeOf = (e: SubscriptionEntry): Place => ({
  kind: (e.kind === "bill" ? "bill" : "subscription") as Kind,
  group: e.group ?? "Other subscriptions",
});

function Ask({ entry }: { entry: SubscriptionEntry }) {
  const client = useQueryClient();
  const [place, setPlace] = useState(() => placeOf(entry));
  const name = entry.display_name ?? entry.merchant;
  const done = { onSettled: () => void client.invalidateQueries({ queryKey: SUBS_KEY }) };
  const yes = useMutation({ mutationFn: () => confirmSubscription(entry.merchant_id, place), ...done });
  const no = useMutation({ mutationFn: () => dismissSubscription(entry.merchant_id), ...done });
  const busy = yes.isPending || no.isPending;
  const error = yes.error ?? no.error;
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="font-semibold">Is {name} a subscription?</p>
        <p className="text-label text-muted-foreground">{reasonText(entry)}</p>
        {error && (
          <p role="alert" className="mt-1 text-label text-warning-foreground">
            {error.message}
          </p>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <PlacementSelect label={`Group for ${name}`} value={place} onChange={setPlace} disabled={busy} />
        <button type="button" disabled={busy} onClick={() => yes.mutate()} data-magnetic className={`${btn} bg-primary text-primary-foreground`}>
          Yes
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => no.mutate()}
          className={`${btn} bg-active [@media(hover:hover)]:hover:bg-raised`}
        >
          No
        </button>
      </div>
    </div>
  );
}

/** "Is this a subscription?" for every detection the user has not decided. A yes files it in the
 * chosen group; a no is remembered as "not a subscription". Renders nothing when there is none. */
export function SubscriptionPrompt({ suggested }: { suggested: SubscriptionEntry[] }) {
  const reduce = useReducedMotion();
  return (
    <AnimatePresence initial={false}>
      {suggested.length > 0 && (
        <motion.div
          key="prompt"
          exit={{ opacity: 0, transition: { duration: reduce ? 0.1 : 0.15 } }}
          className="flex flex-col gap-3"
        >
          {suggested.map((s) => (
            <motion.div
              key={s.merchant_id}
              initial={{ opacity: 0, y: reduce ? 0 : 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, transition: { duration: reduce ? 0.1 : 0.15 } }}
              transition={{ duration: reduce ? 0.15 : 0.25, ease: [0.23, 1, 0.32, 1] }}
            >
              <Card aria-label={`Is ${s.display_name ?? s.merchant} a subscription?`} className="p-4 sm:p-5">
                <Ask entry={s} />
              </Card>
            </motion.div>
          ))}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
