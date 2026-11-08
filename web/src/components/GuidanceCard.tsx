import { useQuery } from "@tanstack/react-query";
import { fetchGuidance, type GuidanceCardData } from "@/api/client";
import { Explained } from "@/components/InsightsFeed";
import { Card } from "@/components/ui/card";

const OPS: Record<string, string> = { ">": "above", ">=": "at least", "<": "below", "<=": "at most", "==": "exactly", "!=": "not" };
const words = (s: string) => s.replace(/_/g, " ");
const day = (iso: string) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
const shown = (v: string) => (v.startsWith("£") ? <span className="money">{v}</span> : v);

/** One playbook card (#265): its text, the figures the Engine filled in, and a closed-by-default
 * "Why am I seeing this" listing the condition, the signal values and each fact's checked-on date.
 * Sources are named, never linked. */
export function GuidanceCard({ card }: { card: GuidanceCardData }) {
  return (
    <li className="flex flex-col gap-2 py-4 first:pt-0 last:pb-0">
      <h3 className="font-semibold">{card.title}</h3>
      <p className="text-sm">
        <Explained text={card.body} />
      </p>
      <details className="text-sm">
        <summary className="inline-flex min-h-11 cursor-pointer items-center font-semibold underline-offset-4 hover:underline">
          Why am I seeing this
        </summary>
        <div className="flex flex-col gap-3 pb-1">
          <div>
            <p className="text-label text-muted-foreground">The condition</p>
            <ul>
              {card.why.map((w) => (
                <li key={`${w.signal}${w.op}`}>
                  {words(w.signal)} is {shown(w.actual)}, the card needs it {OPS[w.op] ?? w.op} {shown(w.threshold)}
                </li>
              ))}
            </ul>
          </div>
          {card.facts.length > 0 && (
            <div>
              <p className="text-label text-muted-foreground">Facts it relies on</p>
              <ul>
                {card.facts.map((f) => (
                  <li key={f.id}>
                    {f.label}: {f.shown}. {f.source}, checked on {day(f.checked_on)}
                    {f.may_be_out_of_date && " (may be out of date)"}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p className="text-label text-muted-foreground">Evidence: {card.evidence_id}</p>
        </div>
      </details>
    </li>
  );
}

export function GuidanceCards() {
  const { data } = useQuery({ queryKey: ["guidance"], queryFn: fetchGuidance });
  const cards = Array.isArray(data) ? data.filter((c) => Array.isArray(c?.why)) : []; // an older server may answer otherwise
  if (cards.length === 0) return null;
  return (
    <Card aria-labelledby="guidance-title">
      <h2 id="guidance-title" className="mb-4 text-heading">
        Worth knowing
      </h2>
      <ul className="divide-y divide-border">
        {cards.map((c) => (
          <GuidanceCard key={c.id} card={c} />
        ))}
      </ul>
    </Card>
  );
}
