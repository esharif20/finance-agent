const TITLES: Record<string, string> = {
  recurring: "Recurring payment",
  price_increase: "Price rise",
  category_spike: "Spending spike",
  upcoming_renewal: "Renewal coming",
  shortfall: "Cash squeeze",
};

/** A plain name for an Insight type; an unknown (newer) type is humanised rather than hidden. */
export function typeTitle(type: string): string {
  const known = TITLES[type];
  if (known) return known;
  const words = type.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export const confidenceWord = (percent: number): string =>
  percent >= 80 ? "High confidence" : percent >= 65 ? "Fair confidence" : "Low confidence";
