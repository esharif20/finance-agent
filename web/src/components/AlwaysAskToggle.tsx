import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { fetchMerchants, setAlwaysAsk } from "@/api/client";

export const MERCHANTS_KEY = ["merchants"] as const;

/** A switch: on, the app never guesses this Merchant's Category and asks about each payment. */
export function Switch({ on, label, disabled, onChange }: { on: boolean; label: string; disabled?: boolean; onChange: (on: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!on)}
      className="group inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center disabled:opacity-50"
    >
      <span
        aria-hidden="true"
        className={`relative h-7 w-12 rounded-full transition-colors duration-200 ease-out ${on ? "bg-primary" : "bg-[var(--control)]"}`}
      >
        <span
          className={`absolute top-0.5 left-0.5 size-6 rounded-full bg-card shadow-sm transition-transform duration-300 ease-[cubic-bezier(0.34,1.56,0.64,1)] group-active:scale-[0.94] motion-reduce:transition-none ${on ? "translate-x-5" : ""}`}
        />
      </span>
    </button>
  );
}

/** The "Always ask" toggle for one Merchant, with the app's suggestion when it has one. */
export function AlwaysAskToggle({ merchantId, name }: { merchantId: number; name: string }) {
  const client = useQueryClient();
  const merchants = useQuery({ queryKey: MERCHANTS_KEY, queryFn: fetchMerchants });
  const [message, setMessage] = useState("");
  const set = useMutation({
    mutationFn: ({ on, source }: { on: boolean; source?: "user" | "suggestion" }) => setAlwaysAsk(merchantId, on, source),
    onSuccess: (r) => {
      setMessage(r.message);
      void client.invalidateQueries();
    },
  });
  const m = merchants.data?.find((x) => x.id === merchantId);
  if (!m) return null;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-label font-semibold">Always ask for {name}</p>
          <p className="text-meta text-muted-foreground">Don't guess a Category here. Each payment waits in To review.</p>
        </div>
        <Switch on={m.always_ask} label={`Always ask for ${name}`} disabled={set.isPending} onChange={(on) => set.mutate({ on })} />
      </div>
      {m.suggest_always_ask && !m.always_ask && (
        <p className="flex flex-wrap items-center gap-2 rounded-[14px] bg-raised px-3 py-2 text-sm">
          You've put {name} in different Categories. Ask each time instead?
          <button
            type="button"
            onClick={() => set.mutate({ on: true, source: "suggestion" })}
            disabled={set.isPending}
            className="min-h-11 rounded-full bg-active px-4 font-semibold"
          >
            Turn on
          </button>
        </p>
      )}
      <p role="status" className="text-label text-muted-foreground empty:hidden">
        {set.error ? set.error.message : message}
      </p>
    </div>
  );
}
