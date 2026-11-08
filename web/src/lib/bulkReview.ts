import type { ReviewItem } from "@/api/client";

export type Cat = [string, string];
export type BulkGroup = {
  key: string;
  category: Cat;
  items: ReviewItem[];
  score: number;
  /** Merchants that start unticked: those whose score is below the group's median. */
  off: ReadonlySet<string>;
};

/** At least this many suggested Category items makes Bulk the default view. */
export const BULK_MIN = 5;

/** A group with more Merchants than this starts collapsed and is accepted this many at a time. */
export const GROUP_CAP = 15;

/**
 * Spend x uncertainty, in minor units. Uncertainty is how many other Categories the model offered
 * (the list holds up to 3, best first), so a row it is torn about is asked about sooner.
 */
export const score = (i: ReviewItem) => i.spend_minor * Math.max(1, i.suggestions.length);

export const bulkable = (i: ReviewItem) => i.type === "category" && !i.skipped && i.suggestions.length > 0;

const median = (xs: number[]) => {
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? (s[m] as number) : ((s[m - 1] as number) + (s[m] as number)) / 2;
};

/** Category items grouped by their top suggestion; groups and items biggest score first, ties by title. */
export function groupBySuggestion(items: ReviewItem[]): BulkGroup[] {
  const by = new Map<string, BulkGroup>();
  for (const i of items.filter(bulkable)) {
    const category = i.suggestions[0] as Cat;
    const key = category.join("/");
    const g = by.get(key) ?? { key, category, items: [], score: 0, off: new Set<string>() };
    g.items.push(i);
    g.score += score(i);
    by.set(key, g);
  }
  const byScore = (a: ReviewItem, b: ReviewItem) => score(b) - score(a) || a.title.localeCompare(b.title);
  return [...by.values()]
    .map((g) => {
      const mid = median(g.items.map(score));
      return { ...g, items: g.items.sort(byScore), off: new Set(g.items.filter((i) => score(i) < mid).map((i) => i.id)) };
    })
    .sort((a, b) => b.score - a.score || a.key.localeCompare(b.key));
}

export const isBig = (g: BulkGroup) => g.items.length > GROUP_CAP;
export const pages = (g: BulkGroup) => Math.max(1, Math.ceil(g.items.length / GROUP_CAP));
/** The rows a group offers now: all of a small group, else one page of GROUP_CAP. */
export const shown = (g: BulkGroup, page: number) => {
  const p = Math.min(Math.max(page, 0), pages(g) - 1);
  return isBig(g) ? g.items.slice(p * GROUP_CAP, (p + 1) * GROUP_CAP) : g.items;
};

/** Ticked unless below the group's median, with the user's changes (`flipped`) on top of that default. */
export const ticked = (g: BulkGroup, flipped: ReadonlySet<string>, id: string) => g.off.has(id) === flipped.has(id);

/** What "Accept N" would send: the ticked rows of what is shown. */
export const selected = (g: BulkGroup, flipped: ReadonlySet<string>, page = 0) =>
  shown(g, page).filter((i) => ticked(g, flipped, i.id));

/** Payments behind some Merchants (a count, never an amount). */
export const payments = (items: ReviewItem[]) => items.reduce((n, i) => n + i.weight, 0);
