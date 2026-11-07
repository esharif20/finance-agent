/**
 * Where to manage a subscription, by Merchant. A reviewed, built-in list: a link is
 * never taken from an email, a CSV row or a description (phishing), and an unknown
 * Merchant gets none. Every url is https on the entry's own `domain`.
 * Each entry: source = the official page reached, checked = date its url was fetched
 * and answered (2026-11-07, live HTTP check; sign-in redirects count as answered).
 * Not listed because no official url could be confirmed: NOW, EE.
 */
type Entry = { names: string[]; domain: string; url: string };

export const MANAGE_LINKS: readonly Entry[] = [
  // source: netflix.com/youraccount, checked 2026-11-07
  { names: ["Netflix", "Netflix.com"], domain: "netflix.com", url: "https://www.netflix.com/youraccount" },
  // source: spotify.com/account, checked 2026-11-07
  { names: ["Spotify"], domain: "spotify.com", url: "https://www.spotify.com/account/" },
  // source: amazon.co.uk/gp/primecentral (-> Membership Central), checked 2026-11-07
  { names: ["Amazon Prime", "Prime Video", "Amazon Prime Video"], domain: "amazon.co.uk", url: "https://www.amazon.co.uk/gp/primecentral" },
  // source: disneyplus.com/account (-> /commerce/account), checked 2026-11-07
  { names: ["Disney Plus", "Disney+", "DisneyPlus"], domain: "disneyplus.com", url: "https://www.disneyplus.com/account" },
  // source: youtube.com/paid_memberships, checked 2026-11-07
  { names: ["YouTube Premium", "Youtube Member", "Google YouTube"], domain: "youtube.com", url: "https://www.youtube.com/paid_memberships" },
  // source: apps.apple.com/account/subscriptions (-> Apple ID sign-in), checked 2026-11-07
  { names: ["Apple", "Apple.com/bill", "Apple Bill", "Apple Services"], domain: "apple.com", url: "https://apps.apple.com/account/subscriptions" },
  // source: account.microsoft.com/services (-> Microsoft sign-in), checked 2026-11-07
  { names: ["Microsoft 365", "Microsoft", "Microsoft Office 365"], domain: "microsoft.com", url: "https://account.microsoft.com/services" },
  // source: account.adobe.com/plans, checked 2026-11-07
  { names: ["Adobe", "Adobe Creative Cloud"], domain: "adobe.com", url: "https://account.adobe.com/plans" },
  // source: one.google.com/settings (-> Google sign-in), checked 2026-11-07
  { names: ["Google One", "Google Storage"], domain: "google.com", url: "https://one.google.com/settings" },
  // source: audible.co.uk/account (-> Amazon sign-in), checked 2026-11-07
  { names: ["Audible"], domain: "audible.co.uk", url: "https://www.audible.co.uk/account" },
  // source: three.co.uk/my3 (-> /account), checked 2026-11-07
  { names: ["Three", "Three UK"], domain: "three.co.uk", url: "https://www.three.co.uk/account" },
  // source: o2.co.uk/myo2 (-> /myvmo2), checked 2026-11-07
  { names: ["O2", "Virgin Media O2"], domain: "o2.co.uk", url: "https://www.o2.co.uk/myo2" },
  // source: vodafone.co.uk/myvodafone (-> My Vodafone sign-in), checked 2026-11-07
  { names: ["Vodafone"], domain: "vodafone.co.uk", url: "https://www.vodafone.co.uk/myvodafone" },
  // source: giffgaff.com/auth/login, checked 2026-11-07
  { names: ["Giffgaff"], domain: "giffgaff.com", url: "https://www.giffgaff.com/auth/login" },
  // source: virginmedia.com/my-virgin-media, checked 2026-11-07
  { names: ["Virgin Media"], domain: "virginmedia.com", url: "https://www.virginmedia.com/my-virgin-media" },
  // source: sky.com/mysky, checked 2026-11-07
  { names: ["Sky"], domain: "sky.com", url: "https://www.sky.com/mysky" },
  // source: bt.com/mybt (-> my.bt.com), checked 2026-11-07
  { names: ["BT"], domain: "bt.com", url: "https://www.bt.com/mybt" },
];

/** "Apple.com/BILL" and "APPLE COM BILL" are the same Merchant. */
const key = (s: string) => s.toUpperCase().replace(/[^A-Z0-9+]/g, "");

const BY_NAME = new Map(MANAGE_LINKS.flatMap((e) => e.names.map((n) => [key(n), e] as const)));

/** The built-in manage url for a Merchant name, or null. Re-checks https and domain. */
export function manageUrl(merchant: string | null | undefined): string | null {
  const e = merchant ? BY_NAME.get(key(merchant)) : undefined;
  if (!e) return null;
  const u = new URL(e.url);
  const onDomain = u.hostname === e.domain || u.hostname.endsWith(`.${e.domain}`);
  return u.protocol === "https:" && onDomain ? e.url : null;
}

/** Subscriptions or Streaming, or a child of either. `category` is [top, sub]. */
export const isSubscription = (category: readonly string[] | null | undefined) =>
  !!category && (category[0] === "Subscriptions" || category.includes("Streaming"));
