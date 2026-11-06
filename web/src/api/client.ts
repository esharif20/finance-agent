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

const query = (month?: string, accountId?: number) => {
  const q = new URLSearchParams();
  if (month) q.set("month", month);
  if (accountId !== undefined) q.set("account_id", String(accountId));
  return q.size ? `?${q}` : "";
};

export const fetchSummary = (month?: string, accountId?: number) =>
  get<Summary>(`/api/summary${query(month, accountId)}`);
export const fetchAccounts = () => get<Account[]>("/api/accounts");
export const fetchSpending = (month: string, accountId?: number) =>
  get<Spending>(`/api/spending${query(month, accountId)}`);

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
export const fetchMonths = () => get<string[]>("/api/months");
export type MonthTotals = components["schemas"]["MonthTotalsOut"];
export const fetchMonthSeries = (accountId?: number) =>
  get<MonthTotals[]>(
    `/api/month-series?limit=12${accountId === undefined ? "" : `&account_id=${accountId}`}`,
  );

export type CategoryDetail = components["schemas"]["CategoryDetailOut"];
export const fetchCategory = (name: string, month: string) =>
  get<CategoryDetail>(`/api/categories/${encodeURIComponent(name)}?month=${month}`);
