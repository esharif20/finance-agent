import { useQuery } from "@tanstack/react-query";
import type { components } from "@/api/schema";
import { formatMoney } from "@/lib/money";

type Payload = components["schemas"]["PayloadOut"];

/** "What was sent" for one cloud reply (#304). The server stores only the redacted payload, so this shows placeholders, never real values. */
export function PayloadViewer({ conversation, index, cost }: { conversation: string; index: number; cost?: number | null }) {
  const { data, isError } = useQuery({
    queryKey: ["payloads", conversation],
    queryFn: async () => {
      const res = await fetch(`/api/gateway/payloads?conversation=${encodeURIComponent(conversation)}`);
      if (!res.ok) throw new Error(String(res.status));
      return (await res.json()) as Payload[];
    },
  });
  // the log has no per-reply id, so the n-th cloud reply of a thread is its n-th logged payload
  const sent = data?.[index];
  const pence = sent?.cost_pence ?? cost;
  return (
    <details className="mt-2 text-meta text-muted-foreground">
      <summary className="min-h-6 cursor-pointer font-semibold">
        What was sent{pence != null && <> · <span className="tabular-nums">{formatMoney({ minor: pence, currency: "GBP" })}</span></>}
      </summary>
      {sent ? (
        <pre aria-label="Redacted payload" className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-raised p-3 text-meta text-foreground">
          {JSON.stringify(sent.payload, null, 2)}
        </pre>
      ) : (
        <p className="mt-1">{isError ? "Couldn't load what was sent." : "Loading…"}</p>
      )}
    </details>
  );
}
