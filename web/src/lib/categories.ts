import type { Money } from "./money";

/** The donut and the pound strip show the top 5 Categories in colour, the rest grey. */
export const TOP_N = 5;
export const OTHER_COLOUR = "var(--cat-other)";
export const OTHER_NAME = "Other categories";

type CategorySpend = { name: string; amount: Money };

export type Slice = { key: string; name: string; minor: number; colour: string };
export type Row = { name: string; minor: number; percent: number; colour: string };

/** "Food & Drink" -> "var(--cat-food-drink)": one hue per Category on every screen. */
export const categoryColour = (name: string) =>
  `var(--cat-${name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")})`;

/**
 * `categories` arrive largest first. Slices are what gets drawn (a Category whose
 * Refunds exceed its spending has no arc); rows are every Category for the legend.
 */
export function groupSpending(categories: CategorySpend[]): { slices: Slice[]; rows: Row[] } {
  const drawable = categories.filter((c) => c.amount.minor > 0);
  const top = new Set(
    drawable
      .filter((c) => c.name !== "Uncategorised")
      .slice(0, TOP_N)
      .map((c) => c.name),
  );
  const drawn = drawable.reduce((n, c) => n + c.amount.minor, 0);
  const colourOf = (name: string) => (top.has(name) ? categoryColour(name) : OTHER_COLOUR);

  const slices: Slice[] = drawable
    .filter((c) => top.has(c.name))
    .map((c) => ({ key: c.name, name: c.name, minor: c.amount.minor, colour: colourOf(c.name) }));
  const restMinor = drawable.filter((c) => !top.has(c.name)).reduce((n, c) => n + c.amount.minor, 0);
  if (restMinor > 0) {
    slices.push({ key: "other", name: OTHER_NAME, minor: restMinor, colour: OTHER_COLOUR });
  }

  const rows: Row[] = categories.map((c) => ({
    name: c.name,
    minor: c.amount.minor,
    percent: c.amount.minor > 0 && drawn > 0 ? Math.round((c.amount.minor * 100) / drawn) : 0,
    colour: colourOf(c.name),
  }));
  return { slices, rows };
}
