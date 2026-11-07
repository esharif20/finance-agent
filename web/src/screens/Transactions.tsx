import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Search } from "lucide-react";
import { useEffect, useState } from "react";
import {
  type Txn,
  type TxnFilters,
  fetchAccounts,
  fetchCategories,
  fetchTransactions,
  postCorrection,
  revertCorrection,
} from "@/api/client";
import { CategoryChip, type Category } from "@/components/CategoryChip";
import { Money } from "@/components/Money";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

const PAGE = 100;
const KINDS = ["Spending", "Income", "Refund", "Fee", "Transfer"];
const field =
  "min-h-11 rounded-full border border-[var(--control)] bg-card px-4 text-sm font-medium";

const day = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });

function Pick({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="relative">
      <span className="sr-only">{label}</span>
      {children}
      <ChevronDown
        className="pointer-events-none absolute top-1/2 right-4 size-4 -translate-y-1/2 text-muted-foreground"
        aria-hidden="true"
      />
    </label>
  );
}

function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** The Transactions list; `scope` pins filters (the Overview's Account and month) and
 * hides their pickers. */
export function Transactions({ scope = {}, page: size = PAGE }: { scope?: TxnFilters; page?: number }) {
  const client = useQueryClient();
  const [filters, setFilters] = useState<TxnFilters>({});
  const [search, setSearch] = useState("");
  const [limit, setLimit] = useState(size);
  const q = useDebounced(search.trim());
  const applied = { ...filters, ...scope, q };

  const accounts = useQuery({ queryKey: ["accounts"], queryFn: fetchAccounts });
  const categories = useQuery({ queryKey: ["categories"], queryFn: fetchCategories });
  const page = useQuery({
    queryKey: ["transactions", applied, limit],
    queryFn: () => fetchTransactions(applied, limit),
    placeholderData: (prev) => prev,
  });
  // the last change, until it is undone or replaced
  const [undo, setUndo] = useState<{ id: number; label: string } | null>(null);
  const correct = useMutation({
    mutationFn: postCorrection,
    onMutate: () => setUndo(null),
    // totals on Overview and the drill-down come from the same Ledger: refetch everything
    onSuccess: (k, body) => {
      setUndo({ id: k.id, label: body.category[1] });
      return client.invalidateQueries();
    },
  });
  const revert = useMutation({
    mutationFn: revertCorrection,
    onSuccess: () => {
      setUndo(null);
      return client.invalidateQueries();
    },
  });

  const set = (key: keyof TxnFilters) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setLimit(size);
    setFilters((f) => ({ ...f, [key]: e.target.value }));
  };
  const accountName = (id: number) => accounts.data?.find((a) => a.id === id);
  const groups = categories.data ?? [];

  const save = (t: Txn, category: Category, all: boolean) =>
    correct.mutate(
      all && t.merchant_id !== null
        ? { merchant_id: t.merchant_id, category }
        : { transaction_id: t.id, category },
    );

  return (
    <div className="flex flex-col gap-4">
      <form
        role="search"
        aria-label="Filter transactions"
        onSubmit={(e) => e.preventDefault()}
        className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap"
      >
        <label className="relative col-span-2 sm:min-w-44 sm:flex-1">
          <span className="sr-only">Search</span>
          <Search
            className="pointer-events-none absolute top-1/2 left-4 size-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden="true"
          />
          <input
            type="search"
            value={search}
            onChange={(e) => {
              setLimit(size);
              setSearch(e.target.value);
            }}
            placeholder="Search description or merchant"
            className={`${field} w-full pl-10`}
          />
        </label>
        {scope.account_id === undefined && scope.month === undefined && <Pick label="Account">
          <select value={filters.account_id ?? ""} onChange={set("account_id")} className={`${field} w-full appearance-none pr-10`}>
            <option value="">All accounts</option>
            {accounts.data?.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name} {a.masked_number}
              </option>
            ))}
          </select>
        </Pick>}
        <Pick label="Kind">
          <select value={filters.kind ?? ""} onChange={set("kind")} className={`${field} w-full appearance-none pr-10`}>
            <option value="">All kinds</option>
            {KINDS.map((k) => (
              <option key={k}>{k}</option>
            ))}
          </select>
        </Pick>
        <Pick label="Category">
          <select value={filters.category ?? ""} onChange={set("category")} className={`${field} w-full appearance-none pr-10`}>
            <option value="">All categories</option>
            {groups.map((g) => (
              <option key={g.name}>{g.name}</option>
            ))}
          </select>
        </Pick>
        {scope.month === undefined && <label className={`${field} flex items-center gap-2`}>
          <span className="text-muted-foreground">Month</span>
          <input
            type="month"
            value={filters.month ?? ""}
            onChange={set("month")}
            className="min-w-0 flex-1 bg-transparent"
          />
        </label>}
      </form>

      {page.error ? (
        <Card role="alert">
          <h2 className="text-heading">Can't load transactions</h2>
          <p className="mt-2 text-muted-foreground">Run `fa` in a terminal to start the local server, then try again.</p>
        </Card>
      ) : !page.data ? (
        <Skeleton className="h-[480px] rounded-card" />
      ) : (
        <Card className="p-2 sm:p-3" aria-busy={page.isFetching}>
          <p role="status" className="flex min-h-12 flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm text-muted-foreground">
            <span>{page.data.total === 0 ? "No transactions match." : `${page.data.total} transactions`}</span>
            {correct.isError && <span className="text-foreground">{correct.error.message}</span>}
            {revert.isError && <span className="text-foreground">{revert.error.message}</span>}
            {undo && (
              <span className="inline-flex items-center gap-2 text-foreground">
                Moved to {undo.label}.
                <button
                  type="button"
                  onClick={() => revert.mutate(undo.id)}
                  disabled={revert.isPending}
                  className="min-h-8 rounded-full bg-active px-3 font-semibold transition-transform duration-150 ease-out active:scale-[0.97] disabled:opacity-60"
                >
                  Undo
                </button>
              </span>
            )}
          </p>
          <ul aria-label="Transactions">
            {page.data.items.map((t) => (
              <li
                key={t.id}
                aria-label={`${t.description}, ${day(t.posted_on)}`}
                className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1.5 rounded-[12px] px-3 py-3 hover:bg-raised sm:grid-cols-[96px_1fr_auto_auto]"
              >
                <time dateTime={t.posted_on} className="order-3 text-label text-muted-foreground sm:order-none">
                  {day(t.posted_on)}
                </time>
                <div className="order-1 min-w-0 sm:order-none">
                  <p className="truncate text-row font-semibold">{t.merchant ?? t.description}</p>
                  <p className="truncate text-label font-normal text-muted-foreground">
                    {t.merchant ? `${t.description} · ` : ""}
                    {accountName(t.account_id)?.masked_number}
                  </p>
                </div>
                <div className="order-2 text-right sm:order-none sm:min-w-28">
                  <Money
                    value={t.amount}
                    sign
                    className={`text-row font-semibold tracking-[-0.01em] ${t.amount.minor > 0 ? "text-positive" : ""}`}
                  />
                </div>
                <div className="order-4 justify-self-end sm:order-none sm:min-w-44 sm:justify-self-start">
                  {t.kind === "Spending" || t.kind === "Refund" ? (
                    <CategoryChip
                      value={t.category as Category | null}
                      groups={groups}
                      merchant={t.merchant}
                      onChange={(c, all) => save(t, c, all)}
                    />
                  ) : (
                    <span className="text-label text-muted-foreground">{t.kind}</span>
                  )}
                </div>
              </li>
            ))}
          </ul>
          {page.data.items.length < page.data.total && (
            <button
              type="button"
              onClick={() => setLimit((l) => l + size)}
              className="mx-3 my-3 min-h-11 rounded-full bg-active px-5 text-sm font-semibold"
            >
              Show more
            </button>
          )}
        </Card>
      )}
    </div>
  );
}
