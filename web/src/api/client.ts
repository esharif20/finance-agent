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
export type SearchHits = components["schemas"]["SearchOut"];
export const fetchSearch = (q: string) => get<SearchHits>(`/api/search?${new URLSearchParams({ q })}`);
export const fetchAccounts =() => get<Account[]>("/api/accounts");
export const fetchSpending = (month: string, accountId?: number) =>
  get<Spending>(`/api/spending${query(month, accountId)}`);

export type FxMonth = components["schemas"]["FxMonthOut"];
export type FxBalances = components["schemas"]["BalancesOut"];
export const fetchFxMonth = (month: string, accountId?: number) =>
  get<FxMonth>(`/api/fx/month${query(month, accountId)}`);
export const fetchFxBalances = () => get<FxBalances>("/api/fx/balances");

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
export const fetchTransaction = (id: number) => get<TxnPage["items"][number]>(`/api/transactions/${id}`);
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
export type Story = components["schemas"]["StoryOut"];
export const fetchStory = (month: string) => get<Story>(`/api/story/${month}`);
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

export type ReviewItem = components["schemas"]["finance_agent__api__review_queue__ItemOut"];
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
    const stale = (detail as { stale_ids?: string[] } | null)?.stale_ids;
    throw new Error(
      typeof detail === "string"
        ? detail
        : stale
          ? `${stale.length} of those changed since they were shown, so nothing was saved.`
          : "Couldn't save that. Try again.",
    );
  }
  return (await res.json()) as ReviewQueueOut;
}
const itemUrl = (id: string, verb: string) => `/api/review-queue/${encodeURIComponent(id)}/${verb}`;

export const answerReview = (item: ReviewItem, answer: ReviewAnswer) =>
  review(itemUrl(item.id, "answer"), { version: item.version, ...answer });
export const skipReview = (item: ReviewItem) => review(itemUrl(item.id, "skip"), { version: item.version });
/** `count` undoes a whole batch (the `applied` a batch answer returned). */
export const undoReview = (count = 1) => review(count > 1 ? `/api/review-queue/undo?count=${count}` : "/api/review-queue/undo");

/** How many Merchants the latest auto-applied batch holds; 0 means there is none. */
export const fetchAutoBatch = () => get<{ count: number }>("/api/review-queue/auto-batch");

/** Takes back the latest auto-applied batch; its Merchants return to the queue. */
export const undoAutoBatch = () => review("/api/review-queue/undo-auto");

/** All or nothing: a stale item refuses the batch and the error names how many changed. */
export const answerReviewBatch = (items: ReviewItem[], category: [string, string]) =>
  review("/api/review-queue/answers", {
    answers: items.map((i) => ({ id: i.id, version: i.version, category })),
  });

// Splits, notes and "Always ask" (#122). A refusal throws the API's own reason; it never quotes a note.
export type SplitOut = components["schemas"]["SplitOut"];
export type SplitIn = components["schemas"]["SplitIn"];
export type NoteOut = components["schemas"]["NoteOut"];
export type MerchantRow = components["schemas"]["MerchantOut"];
export type AlwaysAskResult = components["schemas"]["AlwaysAskOut"];

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const j: unknown = await res.json().catch(() => null);
    const detail = (j as { detail?: unknown } | null)?.detail;
    const stale = (detail as { stale_ids?: string[] } | null)?.stale_ids;
    throw new Error(
      typeof detail === "string"
        ? detail
        : stale
          ? `${stale.length} of those changed since they were shown, so nothing was saved.`
          : "Couldn't save that. Try again.",
    );
  }
  return (await res.json()) as T;
}
const json = (method: string, body: object): RequestInit => ({
  method,
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

/** The live Split of a Transaction, or null when it has none. */
export async function fetchSplit(txn: number): Promise<SplitOut | null> {
  const res = await fetch(`/api/splits/${txn}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`split ${txn}: ${res.status}`);
  return (await res.json()) as SplitOut;
}
export const saveSplit = (txn: number, parts: SplitIn["parts"]) =>
  call<SplitOut>(`/api/splits/${txn}`, json("PUT", { parts }));
export const revertSplit = (txn: number) => call<{ id: number }>(`/api/splits/${txn}`, { method: "DELETE" });

const notePath = (txn: number, part?: number | null) =>
  `/api/notes/${txn}${part === null || part === undefined ? "" : `?part=${part}`}`;
/** The live note, or null when there is none. */
export async function fetchNote(txn: number, part?: number | null): Promise<NoteOut | null> {
  const res = await fetch(notePath(txn, part));
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`note ${txn}: ${res.status}`);
  return (await res.json()) as NoteOut;
}
export const saveNote = (txn: number, text: string, part?: number | null) =>
  call<NoteOut>(notePath(txn, part), json("PUT", { text }));
export const purgeNote = (txn: number, part?: number | null) =>
  call<{ purged: number }>(notePath(txn, part), { method: "DELETE" });

export const fetchMerchants = () => get<{ items: MerchantRow[] }>("/api/merchants").then((r) => r.items);
export const setAlwaysAsk = (merchant: number, on: boolean, source: "user" | "suggestion" = "user") =>
  call<AlwaysAskResult>(`/api/merchants/${merchant}/always-ask`, json("PUT", { on, source }));

// The Assistant's model (Settings): "fast" is Faster, "careful" is More careful.
export type AssistantModel = "fast" | "careful";
export const fetchAssistantModel = () => get<{ model: AssistantModel }>("/api/chat/model").then((r) => r.model);
export const setAssistantModel = (model: AssistantModel) =>
  call<{ model: AssistantModel }>("/api/chat/model", json("PUT", { model }));

// Subscriptions (#101). Costs may carry flags and trials (#106); lib/subscriptions types them.
export type SubscriptionEntry = components["schemas"]["SubscriptionOut"];
export type SubscriptionCosts = import("@/lib/subscriptions").Costs;
export type Placement = { kind: "subscription" | "bill"; group: string };
// The built-in Manage links (#174): the reviewed list is served by the API.
export type ManageLinkEntry = components["schemas"]["ManageLinkOut"];
export const fetchManageLinks = () => get<ManageLinkEntry[]>("/api/manage-links");
export const fetchSubscriptions =() => get<SubscriptionEntry[]>("/api/subscriptions");
export const fetchSubscriptionCosts = () => get<SubscriptionCosts>("/api/subscriptions/costs");

const sub = (merchant: number, verb = "") => `/api/subscriptions/${merchant}${verb}`;
export const addSubscription = (merchant: number, p: Placement) =>
  call<SubscriptionEntry>("/api/subscriptions", json("POST", { merchant_id: merchant, ...p }));
export const regroupSubscription = (merchant: number, p: Placement) =>
  call<SubscriptionEntry>(sub(merchant), json("PATCH", p));
export const confirmSubscription = (merchant: number, p?: Placement) =>
  call<SubscriptionEntry>(sub(merchant, "/confirm"), json("POST", p ?? {}));
export const dismissSubscription = (merchant: number) =>
  call<SubscriptionEntry>(sub(merchant, "/dismiss"), { method: "POST" });
export const removeSubscription = (merchant: number) => call<SubscriptionEntry>(sub(merchant), { method: "DELETE" });
export const cancelSubscription = (merchant: number) =>
  call<SubscriptionEntry>(sub(merchant, "/cancel"), { method: "POST" });

// Insights (#150) and the what-if check (#151). A refusal throws the API's own reason.
export type Insight = components["schemas"]["InsightOut"];
export type Simulation = components["schemas"]["SimulationOut"];
export type GuidanceCardData = components["schemas"]["CardOut"];
export const fetchGuidance = () => get<GuidanceCardData[]>("/api/guidance");
export const fetchInsights =() => get<Insight[]>("/api/insights?limit=5");

async function post(url: string, body?: object): Promise<void> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: body && JSON.stringify(body),
  });
  if (!res.ok) throw new Error("Couldn't save that. Try again.");
}
export const rateInsight = (id: number, liked: boolean) => post(`/api/insights/${id}/feedback`, { liked });
export const dismissInsight = (id: number) => post(`/api/insights/${id}/dismiss`);

export async function simulatePurchase(amountMinor: number, on: string, bufferMinor: number): Promise<Simulation> {
  const q = new URLSearchParams({ amount_minor: String(amountMinor), on, buffer_minor: String(bufferMinor) });
  const res = await fetch(`/api/cash-flow/simulate-purchase?${q}`);
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => null);
    const detail = (body as { detail?: unknown } | null)?.detail;
    throw new Error(typeof detail === "string" ? detail : "Couldn't check that. Try again.");
  }
  return (await res.json()) as Simulation;
}

// Forecast band, weekly briefing and regret review (#152), and what the app has learned (#154).
export type ForecastBand = components["schemas"]["finance_agent__api__cash_flow__BandOut"];
export type Briefing = components["schemas"]["BriefingOut"];
export type RegretItem = components["schemas"]["RegretItemOut"];
export type Memory = components["schemas"]["MemoryOut"];
export const fetchForecast = () => get<ForecastBand[]>("/api/cash-flow/forecast");
export type Goals = components["schemas"]["GoalsOut"];
export const fetchGoals = () => get<Goals>("/api/goals");
export type AllocationPlan = components["schemas"]["AllocationPlanOut"];
export const fetchAllocationPlan = () => get<AllocationPlan>("/api/plan/allocation");
export const fetchBriefing = () => get<Briefing>("/api/briefing");
export const fetchMemories = () => get<{ items: Memory[] }>("/api/memories").then((r) => r.items);

async function wipe(url: string): Promise<void> {
  const res = await fetch(url, { method: "DELETE" });
  if (!res.ok) throw new Error("Couldn't reset that. Try again.");
}
export const answerRegret = async (txn: number, worthIt: boolean): Promise<void> => {
  const res = await fetch(`/api/briefing/regret/${txn}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ worth_it: worthIt }),
  });
  if (!res.ok) throw new Error("Couldn't save that. Try again.");
};
export const resetInsightFeedback = () => wipe("/api/insights/feedback");

// The daily news briefing (#309): topic names are the only thing that leaves the Mac.
export type News = components["schemas"]["NewsOut"];
export type NewsTopic = components["schemas"]["TopicOut"];
export type NewsCard = components["schemas"]["NewsCardOut"];
export const fetchNews = () => get<News>("/api/briefing/news");
export const setNewsTopic = async (name: string, state: "on" | "off"): Promise<void> => {
  const res = await fetch("/api/briefing/topics", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name, state }),
  });
  if (!res.ok) throw new Error(res.status === 422 ? "Use words only, 2 to 40 letters." : "Couldn't save that. Try again.");
};
export const forgetNewsTopic = (name: string) => wipe(`/api/briefing/topics/${encodeURIComponent(name)}`);
export const markNewsRead = async (id: number): Promise<void> => {
  const res = await fetch(`/api/briefing/news/${id}/read`, { method: "POST" });
  if (!res.ok) throw new Error("Couldn't save that. Try again.");
};
export const resetRegretAnswers = () => wipe("/api/briefing/regret");
export const resetAssistantFeedback = () => wipe("/api/feedback");
export const forgetMemory = (id: number) => wipe(`/api/memories/${id}`);

export type KnowYou = components["schemas"]["KnowYouOut"];
export type Learned = components["schemas"]["LearnedOut"];
export const fetchKnowYou = () => get<KnowYou>("/api/know-you");
export const fetchLearned = () => get<Learned>("/api/preferences?question=cut");
export const resetPreferences = () => wipe("/api/preferences");
export const resetBandit = () => wipe("/api/know-you/bandit");
export const resetLifeChanges = () => wipe("/api/know-you/life-changes");
export const resetMap = () => wipe("/api/know-you/map");

export type Pair = components["schemas"]["PairOut"];
export const fetchNextPair = () => get<Pair | null>("/api/preferences/next-pair?question=cut");
export const answerPair = async (pairId: number, choice: string): Promise<void> => {
  const res = await fetch("/api/preferences/answer", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ pair_id: pairId, choice }),
  });
  if (!res.ok) throw new Error("Couldn't save that. Try again.");
};
