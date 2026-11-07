import { useEffect, useState } from "react";

/** Hash routes: "" is the Overview (All accounts); "#account/<id>" is the Overview for one
 * Account; "#category/<name>/<YYYY-MM>[/<account id>]" is a drill-down. */
export type Route =
  | { name: "overview"; account?: number }
  | { name: "transactions" }
  | { name: "review" }
  | { name: "category"; category: string; month?: string; account?: number };

const id = (s: string | undefined) => (s && /^\d+$/.test(s) ? Number(s) : undefined);

export function parseHash(hash: string): Route {
  const [kind, name, month, account] = hash.replace(/^#/, "").split("/");
  if (kind === "transactions") return { name: "transactions" };
  if (kind === "review") return { name: "review" };
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

export function useRoute(): Route {
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return parseHash(hash);
}
