import { Check, TriangleAlert } from "lucide-react";
import type { CSSProperties } from "react";
import type { Account, Spending, Summary } from "@/api/client";
import { Money } from "@/components/Money";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { accountHue } from "./AccountStack";

const when = (iso: string) =>
  new Date(iso).toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

const SOURCE: Record<string, string> = { csv: "a CSV statement" };

/** One Account's details: balance, whether the Ledger agrees, and its last import. */
export function AccountDetails({ account: a }: { account: Account }) {
  const matches = a.gaps.length === 0;
  return (
    <Card aria-labelledby="account-title" className="flex flex-col gap-5">
      <CardHeader className="mb-0">
        <div className="min-w-0">
          <CardTitle id="account-title" className="truncate">
            {a.bank} {a.masked_number}
          </CardTitle>
          <CardDescription>{a.name}</CardDescription>
        </div>
      </CardHeader>
      <dl className="grid grid-cols-2 gap-4">
        <div role="group" aria-label="Bank balance">
          <dt className="text-label text-muted-foreground">Bank balance</dt>
          <dd className="mt-0.5">
            <Money value={a.bank_balance} className="figure text-figure" />
          </dd>
        </div>
        <div role="group" aria-label="Ledger balance">
          <dt className="text-label text-muted-foreground">Ledger balance</dt>
          <dd className="mt-0.5">
            <Money value={a.ledger_balance} className="figure text-figure" />
          </dd>
        </div>
      </dl>
      <p
        role="status"
        className={`flex items-start gap-2 rounded-[10px] p-3 text-sm ${matches ? "bg-raised" : "bg-warning text-warning-foreground"}`}
      >
        {matches ? (
          <Check className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        ) : (
          <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        )}
        <span>
          {matches
            ? "Matches ledger. Every pound your bank shows is accounted for."
            : `${a.gaps.length === 1 ? "One Reconciliation Gap" : `${a.gaps.length} Reconciliation Gaps`} between the bank and the ledger. The dates are under the cards.`}
        </span>
      </p>
      {a.last_import ? (
        <p className="text-label text-muted-foreground">
          Last imported {when(a.last_import)}, from {SOURCE[a.source] ?? a.source}.
        </p>
      ) : null}
    </Card>
  );
}

/** Money moved between the user's own Accounts: never money in or out. */
export function TransfersLine({ summary, combined }: { summary: Summary; combined: boolean }) {
  const { transfers_in: tin, transfers_out: tout } = summary;
  if (tin.minor === 0 && tout.minor === 0) return null;
  const one = combined && tin.minor === tout.minor;
  return (
    <p role="group" aria-label="Transfers between your accounts" className="text-sm text-muted-foreground">
      {one ? (
        <>
          <Money value={tout} className="font-semibold text-foreground" /> moved between your
          accounts, left out of money in and out.
        </>
      ) : (
        <>
          Transfers between your accounts, left out of money in and out:{" "}
          <Money value={tin} className="font-semibold text-foreground" /> in,{" "}
          <Money value={tout} className="font-semibold text-foreground" /> out.
        </>
      )}
    </p>
  );
}

/** The combined view's per-Account parts: each Account's share of this month's money in
 * and out. The parts add up to the combined figures (the API's test proves it). */
export function AccountShares({ summary, accounts }: { summary: Summary; accounts: Account[] }) {
  const rows = summary.accounts.flatMap((p) => {
    const i = accounts.findIndex((a) => a.id === p.account_id);
    const a = accounts[i];
    return a ? [{ ...p, a, hue: accountHue(a, i) }] : [];
  });
  const bar = (key: "money_in" | "money_out", total: number, label: string) => (
    <div className="flex flex-col gap-1.5">
      <span className="text-label text-muted-foreground">{label}</span>
      <div className="share-bar" aria-hidden="true">
        {total > 0 &&
          rows
            .filter((r) => r[key].minor > 0)
            .map((r) => (
              <span
                key={r.account_id}
                style={{ "--h": r.hue, flexGrow: r[key].minor } as CSSProperties}
              />
            ))}
      </div>
    </div>
  );
  return (
    <Card aria-labelledby="shares-title" className="flex flex-col gap-5">
      <CardHeader className="mb-0">
        <CardTitle id="shares-title">By account</CardTitle>
      </CardHeader>
      {bar("money_in", summary.money_in.minor, "Money in")}
      {bar("money_out", summary.money_out.minor, "Money out")}
      <table className="w-full text-sm">
        <caption className="sr-only">Money in and out by account this month</caption>
        <thead>
          <tr className="text-label text-muted-foreground">
            <th scope="col" className="pb-2 text-left font-medium">Account</th>
            <th scope="col" className="pb-2 text-right font-medium">In</th>
            <th scope="col" className="pb-2 text-right font-medium">Out</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.account_id}>
              <th scope="row" className="py-1.5 text-left font-semibold">
                <span className="flex items-center gap-2">
                  <span className="share-key" style={{ "--h": r.hue } as CSSProperties} aria-hidden="true" />
                  {r.a.bank} {r.a.masked_number}
                </span>
              </th>
              <td className="py-1.5 text-right">
                <Money value={r.money_in} />
              </td>
              <td className="py-1.5 text-right">
                <Money value={r.money_out} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

/** The month's biggest Merchants for the Account in view, as the Ledger ranks them. */
export function TopMerchants({ spending }: { spending: Spending }) {
  const top = spending.merchants;
  const max = Math.max(1, ...top.map((m) => m.amount.minor));
  return (
    <Card aria-labelledby="merchants-title" className="flex flex-col gap-4">
      <CardHeader className="mb-0">
        <CardTitle id="merchants-title">Top merchants</CardTitle>
      </CardHeader>
      {top.length === 0 ? (
        <p className="text-sm text-muted-foreground">No spending with a merchant this month.</p>
      ) : (
        <ol aria-label="Top merchants" className="spotlight flex flex-col gap-3">
          {top.map((m) => (
            <li key={m.name} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1">
              <span className="truncate text-row font-semibold">{m.name}</span>
              <Money value={m.amount} className="text-row font-semibold" />
              <span className="merchant-bar col-span-2" aria-hidden="true">
                <span style={{ width: `${(Math.max(0, m.amount.minor) / max) * 100}%` }} />
              </span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}
