/** Split maths in integer pence. No float touches an amount: the last part is the
 * remainder, computed here only to show it live; the Ledger computes it again on save. */

export const MIN_PARTS = 2;
export const MAX_PARTS = 10;

/** "12.50", "£3.2" or "1,204" as pence; null for anything that isn't a positive whole-penny amount. */
export function parsePence(text: string): number | null {
  const m = /^(\d{1,12})?(?:\.(\d{1,2}))?$/.exec(text.replace(/[£,\s]/g, ""));
  if (!m || (m[1] === undefined && m[2] === undefined)) return null;
  const pence = Number(m[1] ?? 0) * 100 + Number((m[2] ?? "").padEnd(2, "0"));
  return pence > 0 ? pence : null;
}

export type SplitState = {
  /** Signed pence of every part, as the Bank has the payment; the last is the remainder. */
  minors: number[];
  /** Signed pence the last part would hold; the wrong sign when the typed parts overshoot. */
  remainder: number;
  valid: boolean;
};

/**
 * `rows` are what you typed, magnitudes only; the last row's amount is ignored (it is the
 * remainder). `total` is the payment's signed minor units.
 */
export function splitState(total: number, rows: { amount: string }[]): SplitState {
  const sign = total < 0 ? -1 : 1;
  const typed = rows.slice(0, -1).map((r) => parsePence(r.amount));
  const sum = typed.reduce<number>((a, p) => a + (p ?? 0), 0);
  const left = Math.abs(total) - sum; // magnitude of the remainder
  const valid =
    rows.length >= MIN_PARTS &&
    rows.length <= MAX_PARTS &&
    typed.every((p) => p !== null) &&
    left > 0;
  return {
    minors: [...typed.map((p) => sign * (p ?? 0)), sign * left],
    remainder: sign * left,
    valid,
  };
}
