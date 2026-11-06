import { Check } from "lucide-react";
import { useId } from "react";
import type { components } from "@/api/schema";
import { Money } from "@/components/Money";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatMoney } from "@/lib/money";

type Account = components["schemas"]["AccountOut"];

const day = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });

function AccountRow({ account }: { account: Account }) {
  const nameId = useId();
  const matches = account.gaps.length === 0;
  return (
    <li aria-labelledby={nameId} className="py-4 first:pt-0 last:pb-0">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p id={nameId} className="truncate text-base font-medium">
            {account.name}
          </p>
          {account.name.includes(account.masked_number) ? null : (
            <p className="text-sm text-muted-foreground">{account.masked_number}</p>
          )}
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <Money value={account.bank_balance} className="text-lg font-semibold" />
          {matches ? (
            <p className="flex items-center gap-1 text-[13px] font-semibold text-muted-foreground">
              <Check className="size-3.5" aria-hidden="true" />
              Matches ledger
            </p>
          ) : null}
        </div>
      </div>
      {matches ? null : (
        <details className="group mt-3">
          <summary className="inline-flex min-h-8 cursor-pointer list-none items-center rounded-full bg-warning px-3 text-[13px] font-semibold text-warning-foreground">
            Find it
          </summary>
          <ul className="mt-3 flex flex-col gap-2 text-sm text-muted-foreground">
            {account.gaps.map((g) => (
              <li key={`${g.from_date}-${g.to_date}`}>
                Between {day(g.from_date)} and {day(g.to_date)} your bank shows{" "}
                <span className="font-semibold text-foreground">
                  {formatMoney(g.amount)}
                </span>{" "}
                {g.amount.minor > 0 ? "more" : "less"} than your ledger. Import the statement
                covering those dates.
              </li>
            ))}
          </ul>
        </details>
      )}
    </li>
  );
}

export function AccountsCard({ accounts }: { accounts: Account[] }) {
  return (
    <Card aria-labelledby="accounts-title">
      <CardHeader>
        <CardTitle id="accounts-title">Accounts</CardTitle>
        <CardDescription>Checked against your ledger</CardDescription>
      </CardHeader>
      {accounts.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No accounts yet. Run <code>fa import</code> with a statement export.
        </p>
      ) : (
        <ul className="flex flex-col divide-y divide-border">
          {accounts.map((a) => (
            <AccountRow key={a.id} account={a} />
          ))}
        </ul>
      )}
    </Card>
  );
}
