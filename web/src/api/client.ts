import type { components } from "./schema";

export type Summary = components["schemas"]["SummaryOut"];
export type Account = components["schemas"]["AccountOut"];
export type Txn = components["schemas"]["TransactionOut"];
export type TxnPage = components["schemas"]["TransactionsOut"];
export type CategoryGroup = components["schemas"]["CategoryOut"];

export type Spending = components["schemas"]["SpendingOut"];

export class NoData extends Error {}

async function get<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (res.status === 404) throw new NoData(url);
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return (await res.json()) as T;
}

export const fetchSummary = (month?: string) =>
  get<Summary>(month ? `/api/summary?month=${month}` : "/api/summary");
export const fetchAccounts = () => get<Account[]>("/api/accounts");

export type TxnFilters = {
  account_id?: string;
  kind?: string;
  category?: string;
  month?: string;
  q?: string;
};

export const fetchTransactions = (f: TxnFilters, limit: number) => {
  const p = new URLSearchParams({ limit: String(limit) });
  for (const [k, v] of Object.entries(f)) if (v) p.set(k, v);
  return get<TxnPage>(`/api/transactions?${p}`);
};
export const fetchCategories = () => get<CategoryGroup[]>("/api/categories");

export async function postCorrection(body: {
  transaction_id?: number;
  merchant_id?: number;
  category: [string, string];
}): Promise<void> {
  const res = await fetch("/api/corrections", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`correction: ${res.status}`);
}
export const fetchSpending = (month: string) => get<Spending>(`/api/spending?month=${month}`);
export const fetchMonths = () => get<string[]>("/api/months");
export type MonthTotals = components["schemas"]["MonthTotalsOut"];
export const fetchMonthSeries = () => get<MonthTotals[]>("/api/month-series?limit=12");

export type CategoryDetail = components["schemas"]["CategoryDetailOut"];
export const fetchCategory = (name: string, month: string) =>
  get<CategoryDetail>(`/api/categories/${encodeURIComponent(name)}?month=${month}`);
