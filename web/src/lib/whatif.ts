const MAX_MINOR = 10 ** 13; // the API's own cap (#151)

/** Pounds as typed to integer pence, with no float arithmetic; null when not a positive amount of at most two decimals. */
export function parseAmount(raw: string): number | null {
  const v = raw.replace(/[£,\s]/g, "");
  const m = /^(\d{1,12})(?:\.(\d{1,2}))?$/.exec(v);
  if (!m) return null;
  const minor = Number(m[1]) * 100 + Number((m[2] ?? "").padEnd(2, "0"));
  return minor >= 1 && minor <= MAX_MINOR ? minor : null;
}

export type Risk = "none" | "below_buffer" | "below_zero";

export function riskCopy(risk: Risk): string {
  if (risk === "below_zero") return "This would take your balance below zero before payday.";
  if (risk === "below_buffer") return "This would dip into your buffer before payday.";
  return "Your balance stays above your buffer until payday.";
}
