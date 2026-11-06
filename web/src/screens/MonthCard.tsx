import { TriangleAlert } from "lucide-react";
import type { components } from "@/api/schema";
import { Money } from "@/components/Money";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import type { Slice } from "@/lib/categories";
import { PoundStrip } from "./PoundStrip";

type Summary = components["schemas"]["SummaryOut"];

export function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number) as [number, number];
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString("en-GB", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function MonthCard({ summary, slices }: { summary: Summary; slices?: Slice[] }) {
  const { money_in, money_out, net, gaps } = summary;
  const overspent = net.minor < 0;
  return (
    <Card aria-labelledby="month-title" className="flex flex-col gap-6">
      <CardHeader className="mb-0">
        <CardTitle id="month-title">{monthLabel(summary.month)}</CardTitle>
      </CardHeader>

      <div role="group" aria-label="Net">
        <p className="text-sm text-muted-foreground">Net</p>
        <Money
          value={net}
          sign
          className={`block text-[32px] leading-10 font-semibold tracking-[-0.02em] ${overspent ? "" : "text-positive"}`}
        />
      </div>

      <dl className="grid grid-cols-2 gap-4">
        <div role="group" aria-label="Money in" className="rounded-[10px] bg-raised p-4">
          <dt className="text-sm text-muted-foreground">Money in</dt>
          <dd>
            <Money value={money_in} className="text-[26px] leading-9 font-semibold tracking-[-0.01em]" />
          </dd>
        </div>
        <div role="group" aria-label="Money out" className="rounded-[10px] bg-raised p-4">
          <dt className="text-sm text-muted-foreground">Money out</dt>
          <dd>
            <Money value={money_out} className="text-[26px] leading-9 font-semibold tracking-[-0.01em]" />
          </dd>
        </div>
      </dl>

      <PoundStrip inMinor={money_in.minor} outMinor={money_out.minor} slices={slices} />

      {gaps.length > 0 && (
        <p
          role="status"
          className="flex items-start gap-2 rounded-[10px] bg-warning p-3 text-sm text-warning-foreground"
        >
          <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>
            {gaps.length === 1 ? "A Reconciliation Gap touches" : `${gaps.length} Reconciliation Gaps touch`} this
            month, so these figures may be incomplete. Check your Account balances.
          </span>
        </p>
      )}
    </Card>
  );
}
