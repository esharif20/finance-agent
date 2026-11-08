import type { ManageLinkEntry } from "@/api/client";

/**
 * Where to manage a subscription, by Merchant. The reviewed list lives in the API
 * (categorise/manage_links.py): a link is never taken from an email, a CSV row or a
 * description (phishing), and an unknown Merchant gets none.
 */

/** "Apple.com/BILL" and "APPLE COM BILL" are the same Merchant. */
const key = (s: string) => s.toUpperCase().replace(/[^A-Z0-9+]/g, "");

/** The built-in manage url for a Merchant name, or null. Re-checks https and domain. */
export function manageUrl(merchant: string | null | undefined, links: readonly ManageLinkEntry[] | undefined): string | null {
  if (!merchant || !links) return null;
  const k = key(merchant);
  const e = links.find((l) => l.names.some((n) => key(n) === k));
  if (!e) return null;
  const u = new URL(e.url);
  const onDomain = u.hostname === e.domain || u.hostname.endsWith(`.${e.domain}`);
  return u.protocol === "https:" && onDomain ? e.url : null;
}

/** Subscriptions or Streaming, or a child of either. `category` is [top, sub]. */
export const isSubscription = (category: readonly string[] | null | undefined) =>
  !!category && (category[0] === "Subscriptions" || category.includes("Streaming"));
