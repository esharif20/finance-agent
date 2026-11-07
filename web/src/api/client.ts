import type { components } from "./schema";

export type Summary = components["schemas"]["SummaryOut"];
export type Account = components["schemas"]["AccountOut"];
export type Txn = components["schemas"]["TransactionOut"];
export type TxnPage = components["schemas"]["TransactionsOut"];
export type CategoryGroup = components["schemas"]["CategoryOut"];

export type Spending = components["schemas"]["SpendingOut"];
export type SyncStatus = components["schemas"]["SyncStatusOut"];

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

export type CorrectionOut = components["schemas"]["CorrectionOut"];

/** Send a Correction change; a refusal throws the API's own reason when it gives one. */
async function send(url: string, init: RequestInit): Promise<CorrectionOut> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => null);
    const detail = (body as { detail?: unknown } | null)?.detail;
    throw new Error(typeof detail === "string" ? detail : "Couldn't save that change. Try again.");
  }
  return (await res.json()) as CorrectionOut;
}

export const postCorrection = (body: {
  transaction_id?: number;
  merchant_id?: number;
  category: [string, string];
}) =>
  send("/api/corrections", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });

/** Undo a Correction; the Ledger keeps it in the history, marked reverted. */
export const revertCorrection = (id: number) => send(`/api/corrections/${id}`, { method: "DELETE" });
export const fetchMonths = () => get<string[]>("/api/months");
export const fetchSyncStatus = () => get<SyncStatus>("/api/sync-status");

export type SyncResult = components["schemas"]["SyncOut"];

/** Run a sync now. A refusal throws the API's own reason, which never holds amounts or URLs. */
export async function postSync(): Promise<SyncResult> {
  const res = await fetch("/api/sync", { method: "POST" });
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => null);
    const detail = (body as { detail?: unknown } | null)?.detail;
    throw new Error(typeof detail === "string" ? detail : "Couldn't sync. Try again.");
  }
  return (await res.json()) as SyncResult;
}
export type MonthTotals = components["schemas"]["MonthTotalsOut"];
export const fetchMonthSeries = (accountId?: number) =>
  get<MonthTotals[]>(
    `/api/month-series?limit=12${accountId === undefined ? "" : `&account_id=${accountId}`}`,
  );

export type CategoryDetail = components["schemas"]["CategoryDetailOut"];
export const fetchCategory = (name: string, month: string, accountId?: number) =>
  get<CategoryDetail>(`/api/categories/${encodeURIComponent(name)}${query(month, accountId)}`);

export type ReviewItem = components["schemas"]["ItemOut"];
export type ReviewQueueOut = components["schemas"]["QueueOut"];
export type ReviewAnswer = { category?: [string, string]; transaction_id?: number; choice?: string };

export const fetchReviewQueue = () => get<ReviewQueueOut>("/api/review-queue");

/** A refusal throws the API's own reason (a stale item says so); every write returns the queue as it now stands. */
async function review(url: string, body?: object): Promise<ReviewQueueOut> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: body && JSON.stringify(body),
  });
  if (!res.ok) {
    const j: unknown = await res.json().catch(() => null);
    const detail = (j as { detail?: unknown } | null)?.detail;
    throw new Error(typeof detail === "string" ? detail : "Couldn't save that. Try again.");
  }
  return (await res.json()) as ReviewQueueOut;
}
const itemUrl = (id: string, verb: string) => `/api/review-queue/${encodeURIComponent(id)}/${verb}`;

export const answerReview = (item: ReviewItem, answer: ReviewAnswer) =>
  review(itemUrl(item.id, "answer"), { version: item.version, ...answer });
export const skipReview = (item: ReviewItem) => review(itemUrl(item.id, "skip"), { version: item.version });
export const undoReview = () => review("/api/review-queue/undo");
