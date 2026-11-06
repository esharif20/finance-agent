/** "Of every £1 that came in, Xp went out and Yp stayed." Whole pence only. */
export type PoundStrip = {
  spentPence: number;
  keptPence: number;
  overspent: boolean;
};

export function poundStrip(inMinor: number, outMinor: number): PoundStrip | null {
  if (inMinor <= 0) return null;
  const overspent = outMinor > inMinor;
  const spentPence = overspent ? 100 : Math.round((outMinor * 100) / inMinor);
  return { spentPence, keptPence: 100 - spentPence, overspent };
}
