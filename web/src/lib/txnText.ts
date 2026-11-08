// Pure helpers for the Transactions list rows. addsDetail takes the display name and the stored Merchant together (#223).

const NOISE = new Set(["limited", "ltd", "inc", "llc", "plc", "co", "uk", "gb", "card", "payment", "to", "from", "the"]);
const tokens = (s: string) => s.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
// a reference token carries a digit (P487971923, 12345)
const isRef = (t: string) => /\d/.test(t);

/** True when the description says more than the Merchant name plus corporate suffixes and reference IDs. */
export function addsDetail(merchant: string | null, description: string): boolean {
  if (!merchant) return false;
  const own = new Set(tokens(merchant));
  return tokens(description).some((t) => !own.has(t) && !NOISE.has(t) && !isRef(t));
}

/** Join the non-empty parts with " · ", so no separator dangles. */
export const joinParts = (parts: (string | null | undefined)[]): string =>
  parts.filter((p) => p && p.trim()).join(" · ");

export const monthKey = (iso: string) => iso.slice(0, 7);

export const monthLabel = (key: string) =>
  new Date(`${key}-01T00:00:00Z`).toLocaleDateString("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });
