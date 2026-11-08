import type { ReviewItem } from "@/api/client";

export type Cat = [string, string];
export type BulkGroup = { key: string; category: Cat; items: ReviewItem[]; score: number };

/** At least this many suggested Category items makes Bulk the default view. */
export const BULK_MIN = 5;

/**
 * Spend x uncertainty, in minor units. Uncertainty is how many other Categories the model offered
 * (the list holds up to 3, best first), so a row it is torn about is asked about sooner.
 */
export const score = (i: ReviewItem) => i.spend_minor * Math.max(1, i.suggestions.length);

export const bulkable = (i: ReviewItem) => i.type === "category" && !i.skipped && i.suggestions.length > 0;

/** Category items grouped by their top suggestion; groups and items biggest score first, ties by title. */
export function groupBySuggestion(items: ReviewItem[]): BulkGroup[] {
  const by = new Map<string, BulkGroup>();
  for (const i of items.filter(bulkable)) {
    const category = i.suggestions[0] as Cat;
    const key = category.join("/");
    const g = by.get(key) ?? { key, category, items: [], score: 0 };
    g.items.push(i);
    g.score += score(i);
    by.set(key, g);
  }
  const byScore = (a: ReviewItem, b: ReviewItem) => score(b) - score(a) || a.title.localeCompare(b.title);
  return [...by.values()]
    .map((g) => ({ ...g, items: g.items.sort(byScore) }))
    .sort((a, b) => b.score - a.score || a.key.localeCompare(b.key));
}

/** What "Accept N" would send: the group's items minus the unticked ones. */
export const selected = (g: BulkGroup, unticked: ReadonlySet<string>) => g.items.filter((i) => !unticked.has(i.id));

export const spendOf = (items: ReviewItem[]) => items.reduce((n, i) => n + i.spend_minor, 0);
