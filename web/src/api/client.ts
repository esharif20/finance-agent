import type { components } from "./schema";

export type Summary = components["schemas"]["SummaryOut"];
export type Account = components["schemas"]["AccountOut"];

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
export const fetchMonths = () => get<string[]>("/api/months");
