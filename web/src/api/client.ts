import type { components } from "./schema";

export type Summary = components["schemas"]["SummaryOut"];
export type Account = components["schemas"]["AccountOut"];

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
