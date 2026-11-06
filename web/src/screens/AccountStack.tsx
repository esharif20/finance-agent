import { Check } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import type { components } from "@/api/schema";
import { Money } from "@/components/Money";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

type Account = components["schemas"]["AccountOut"];

// Gradient hues, all outside the violet band 235-290.
const HUES = [172, 38, 345, 140, 15, 95];
const PEEK = 72; // px of each card still visible when another sits in front
const HEIGHT = 156;

const day = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });

type Face = {
  key: string;
  id: number | undefined;
  title: string;
  masked?: string;
  balance: { minor: number; currency: string } | null;
  matches: boolean;
  hue: number | null;
};

function faces(accounts: Account[]): Face[] {
  const currency = accounts[0]?.currency ?? "GBP";
  // GBP only for now, like the summary: an All accounts total needs one currency
  const same = accounts.every((a) => a.currency === currency);
  const all: Face = {
    key: "all",
    id: undefined,
    title: "All accounts",
    balance: same
      ? { minor: accounts.reduce((n, a) => n + a.bank_balance.minor, 0), currency }
      : null,
    matches: accounts.every((a) => a.gaps.length === 0),
    hue: null,
  };
  return [
    all,
    ...accounts.map((a, i) => ({
      key: String(a.id),
      id: a.id,
      title: a.bank,
      masked: a.masked_number,
      balance: a.bank_balance,
      matches: a.gaps.length === 0,
      hue: HUES[i % HUES.length] ?? 200,
    })),
  ];
}

export function AccountStack({
  accounts,
  selected,
  onSelect,
}: {
  accounts: Account[];
  selected?: number;
  onSelect: (id: number | undefined) => void;
}) {
  const reduce = useReducedMotion();
  if (accounts.length === 0) {
    return (
      <Card>
        <h2 className="text-[18px] font-semibold">Accounts</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          No accounts yet. Run <code>fa import</code> with a statement export.
        </p>
      </Card>
    );
  }
  const all = faces(accounts);
  // The chosen card goes to the front (bottom of the stack); the rest keep their order.
  // Visual order only: the DOM order stays put so keyboard focus is never lost.
  const rest = all.filter((f) => f.id !== selected);
  const order = [...rest, ...all.filter((f) => f.id === selected)];
  const current = all.find((f) => f.id === selected) ?? (all[0] as Face);
  const inView = selected === undefined ? accounts : accounts.filter((a) => a.id === selected);
  const gaps = inView.flatMap((a) => a.gaps.map((g) => ({ a, g })));

  return (
    <section aria-labelledby="accounts-title" className="flex flex-col gap-4">
      <CardHeader className="mb-0">
        <CardTitle id="accounts-title">Accounts</CardTitle>
        <CardDescription>Choose one to filter the Overview</CardDescription>
      </CardHeader>
      <div role="group" aria-label="Accounts" className="flex flex-col pb-1">
        {all.map((f) => {
          const pos = order.indexOf(f);
          const front = pos === order.length - 1;
          const on = f === current;
          return (
            <motion.button
              key={f.key}
              type="button"
              layout="position"
              transition={reduce ? { duration: 0 } : { duration: 0.2, ease: [0.2, 0.8, 0.2, 1] }}
              aria-pressed={on}
              onClick={() => onSelect(f.id)}
              className="account-card text-left"
              data-front={front}
              data-selected={on}
              data-all={f.id === undefined}
              style={{
                order: pos,
                zIndex: pos,
                height: HEIGHT,
                marginTop: pos === 0 ? 0 : PEEK - HEIGHT,
                ...(f.hue === null ? {} : { ["--h" as string]: f.hue }),
              }}
            >
              <span className="flex items-start justify-between gap-3">
                <span className="min-w-0">
                  <span className="block truncate text-base font-semibold">{f.title}</span>
                  {f.masked ? <span className="block text-sm opacity-80">{f.masked}</span> : null}
                </span>
                {f.balance ? (
                  <Money value={f.balance} className="text-lg font-semibold" />
                ) : null}
              </span>
              <span className="mt-auto flex items-center gap-1.5 text-[13px] font-semibold">
                {f.matches ? (
                  <>
                    <Check className="size-3.5" aria-hidden="true" />
                    Matches ledger
                  </>
                ) : (
                  <span className="rounded-full bg-warning px-3 py-1 text-warning-foreground">
                    Find it
                  </span>
                )}
              </span>
            </motion.button>
          );
        })}
      </div>
      <p role="status" className="sr-only">
        Showing {current.masked ? `${current.title} ${current.masked}` : current.title}
      </p>
      {gaps.length === 0 ? null : (
        <ul className="flex flex-col gap-2 text-sm text-muted-foreground">
          {gaps.map(({ a, g }) => (
            <li key={`${a.id}-${g.from_date}-${g.to_date}`}>
              {a.bank} {a.masked_number}: between {day(g.from_date)} and {day(g.to_date)} your
              bank shows <Money value={g.amount} className="font-semibold text-foreground" />{" "}
              {g.amount.minor > 0 ? "more" : "less"} than your ledger. Import the statement
              covering those dates.
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
