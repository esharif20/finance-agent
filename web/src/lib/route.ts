import { useEffect, useState } from "react";

/** Hash routes: "" is the Overview (All accounts); "#account/<id>" is the Overview for one
 * Account; "#category/<name>/<YYYY-MM>[/<account id>]" is a drill-down. */
export type Route =
  | { name: "overview"; account?: number }
  | { name: "transactions" }
  | { name: "review" }
  | { name: "settings" }
  | { name: "subscriptions" }
  | { name: "briefing" }
  | { name: "know" }
  | { name: "goals" }
  | { name: "plan" }
  | { name: "story"; month?: string }
  | { name: "assistant" }
  | { name: "category"; category: string; month?: string; account?: number };

const id = (s: string | undefined) => (s && /^\d+$/.test(s) ? Number(s) : undefined);

export function parseHash(hash: string): Route {
  const [kind, name, month, account] = hash.replace(/^#/, "").split("/");
  if (kind === "transactions") return { name: "transactions" };
  if (kind === "review") return { name: "review" };
  if (kind === "settings") return { name: "settings" };
  if (kind === "subscriptions") return { name: "subscriptions" };
  if (kind === "briefing") return { name: "briefing" };
  if (kind === "plan") return { name: "plan" };
  if (kind === "story") return { name: "story", month: name && /^\d{4}-\d{2}$/.test(name) ? name : undefined };
  if (kind === "goals") return { name: "goals" };
  if (kind === "know") return { name: "know" };
  if (kind === "assistant") return { name: "assistant" };
  if (kind === "account" && id(name) !== undefined) return { name: "overview", account: id(name) };
  if (kind === "category" && name) {
    try {
      return { name: "category", category: decodeURIComponent(name), month: month || undefined, account: id(account) };
    } catch {
      return { name: "overview" };
    }
  }
  return { name: "overview" };
}

export const categoryHref = (category: string, month?: string, account?: number) =>
  `#category/${encodeURIComponent(category)}${month ? `/${month}` : ""}${month && account !== undefined ? `/${account}` : ""}`;

/** The Overview for one Account, or for all of them. */
export const accountHref = (account?: number) => (account === undefined ? "#overview" : `#account/${account}`);

/** Go to a hash route in the page: a history entry (Back returns) and a hashchange for useRoute,
    with no navigation, so nothing reloads or jumps. Safari can treat a `location.hash` write as
    a navigation (scroll restore, a flash); pushState never does. */
export function goHash(href: string) {
  if (window.location.hash === href) return;
  const old = window.location.href;
  window.history.pushState(null, "", href);
  window.dispatchEvent(new HashChangeEvent("hashchange", { oldURL: old, newURL: window.location.href }));
}

/** A tab or menu link's click: an in-page hash route through goHash, never a navigation, so the
    app is not torn down and rebuilt. Modified clicks (new tab, new window) keep the browser's own. */
export function onHashLink(e: { button: number; metaKey: boolean; ctrlKey: boolean; shiftKey: boolean; altKey: boolean; defaultPrevented: boolean; preventDefault(): void; currentTarget: { getAttribute(name: string): string | null } }) {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const href = e.currentTarget.getAttribute("href");
  if (!href?.startsWith("#")) return;
  e.preventDefault();
  goHash(href);
}

export function useRoute(): Route {
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return parseHash(hash);
}
