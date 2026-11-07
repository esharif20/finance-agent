import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { fetchMerchants, setAlwaysAsk } from "@/api/client";
import { MERCHANTS_KEY, Switch } from "@/components/AlwaysAskToggle";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

/** The Merchants the Shortcut asks about, one per line, as the Wallet automation's filter takes them. */
export const shortcutList = (names: string[]) => names.join("\n");

export function Settings() {
  const client = useQueryClient();
  const merchants = useQuery({ queryKey: MERCHANTS_KEY, queryFn: fetchMerchants });
  const [copied, setCopied] = useState<"done" | "failed" | null>(null);
  const set = useMutation({
    mutationFn: ({ id, on, source }: { id: number; on: boolean; source?: "user" | "suggestion" }) => setAlwaysAsk(id, on, source),
    onSuccess: () => client.invalidateQueries(),
  });

  if (merchants.isPending) return <Skeleton className="h-64 w-full rounded-card" />;
  if (merchants.isError) return <p role="alert">Couldn't load your Merchants. Try again in a moment.</p>;

  const asked = merchants.data.filter((m) => m.always_ask);
  const suggested = merchants.data.filter((m) => m.suggest_always_ask && !m.always_ask);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(shortcutList(asked.map((m) => m.name)));
      setCopied("done");
    } catch {
      setCopied("failed");
    }
  };

  return (
    <div className="mx-auto flex w-full max-w-xl flex-col gap-6">
      <Card aria-labelledby="always-ask-title" className="flex flex-col gap-5">
        <CardHeader className="mb-0">
          <CardTitle id="always-ask-title">Always ask</CardTitle>
        </CardHeader>
        <p className="text-muted-foreground">
          For these Merchants the app never guesses a Category. Each payment waits in To review until you say what it was.
        </p>

        {asked.length === 0 ? (
          <p>No Merchants yet. Turn it on from a payment's details in Transactions.</p>
        ) : (
          <ul aria-label="Always ask Merchants" className="flex flex-col">
            {asked.map((m) => (
              <li key={m.id} className="flex min-h-14 items-center justify-between gap-3">
                <span className="truncate text-row font-semibold">{m.name}</span>
                <Switch on label={`Always ask for ${m.name}`} disabled={set.isPending} onChange={() => set.mutate({ id: m.id, on: false })} />
              </li>
            ))}
          </ul>
        )}
        {set.error && (
          <p role="alert" className="text-sm">
            {set.error.message}
          </p>
        )}

        {suggested.length > 0 && (
          <div className="flex flex-col gap-2 rounded-[14px] bg-raised p-4">
            <p className="text-label font-semibold">Worth asking about</p>
            <p className="text-sm text-muted-foreground">You've put these in different Categories. The app only suggests; you decide.</p>
            <ul className="flex flex-col">
              {suggested.map((m) => (
                <li key={m.id} className="flex min-h-12 items-center justify-between gap-3">
                  <span className="truncate font-medium">{m.name}</span>
                  <button
                    type="button"
                    disabled={set.isPending}
                    onClick={() => set.mutate({ id: m.id, on: true, source: "suggestion" })}
                    className="min-h-11 rounded-full bg-active px-4 text-sm font-semibold transition-transform duration-150 ease-out active:scale-[0.97]"
                  >
                    Turn on
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </Card>

      <Card aria-labelledby="shortcut-title" className="flex flex-col gap-4">
        <CardHeader className="mb-0">
          <CardTitle id="shortcut-title">Notes at the till</CardTitle>
        </CardHeader>
        <p className="text-muted-foreground">
          In the Shortcuts app, add a Wallet Transaction automation that asks "What was this for?". Paste this list into its
          Merchant filter so it only asks at the shops above.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={copy}
            disabled={asked.length === 0}
            className="inline-flex min-h-11 items-center gap-2 rounded-full bg-primary px-5 text-sm font-semibold text-primary-foreground transition-transform duration-150 ease-out active:scale-[0.97] disabled:opacity-50"
          >
            {copied === "done" ? <Check className="size-4" aria-hidden="true" /> : <Copy className="size-4" aria-hidden="true" />}
            Copy list
          </button>
          <p role="status" className="text-sm text-muted-foreground">
            {copied === "done" && `Copied ${asked.length} ${asked.length === 1 ? "Merchant" : "Merchants"}.`}
            {copied === "failed" && "Couldn't reach the clipboard. Select the list below and copy it."}
          </p>
        </div>
        {copied === "failed" && (
          <textarea readOnly rows={Math.min(asked.length, 8)} value={shortcutList(asked.map((m) => m.name))} aria-label="Merchant list" className="rounded-[14px] border border-[var(--control)] bg-card p-3 text-sm" />
        )}
      </Card>
    </div>
  );
}
