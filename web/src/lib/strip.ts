/** "Of every £1 that came in, Xp went out and Yp stayed." Whole pence only. */
export type PoundStrip = {
  spentPence: number;
  keptPence: number;
  overspent: boolean;
};

/** Shares `spentPence` between weights (largest remainder), so the parts sum exactly. */
export function splitSpent(spentPence: number, weights: number[]): number[] {
  const total = weights.reduce((a, b) => a + b, 0);
  if (total <= 0) return weights.map(() => 0);
  const exact = weights.map((w) => (w * spentPence) / total);
  const parts = exact.map(Math.floor);
  let left = spentPence - parts.reduce((a, b) => a + b, 0);
  const byRemainder = exact
    .map((x, i) => [x - Math.floor(x), i] as const)
    .sort((a, b) => b[0] - a[0] || a[1] - b[1]);
  for (const [, i] of byRemainder) {
    if (left-- <= 0) break;
    parts[i] = (parts[i] ?? 0) + 1;
  }
  return parts;
}

export function poundStrip(inMinor: number, outMinor: number): PoundStrip | null {
  if (inMinor <= 0) return null;
  const overspent = outMinor > inMinor;
  const spentPence = overspent ? 100 : Math.round((outMinor * 100) / inMinor);
  return { spentPence, keptPence: 100 - spentPence, overspent };
}
