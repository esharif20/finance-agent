import { useEffect, useState } from "react";

/** Hash routes: "" is the Overview; "#category/<name>/<YYYY-MM>" is a drill-down. */
export type Route = { name: "overview" } | { name: "category"; category: string; month?: string };

export function parseHash(hash: string): Route {
  const [kind, name, month] = hash.replace(/^#/, "").split("/");
  if (kind === "category" && name) {
    try {
      return { name: "category", category: decodeURIComponent(name), month: month || undefined };
    } catch {
      return { name: "overview" };
    }
  }
  return { name: "overview" };
}

export const categoryHref = (category: string, month?: string) =>
  `#category/${encodeURIComponent(category)}${month ? `/${month}` : ""}`;

export function useRoute(): Route {
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return parseHash(hash);
}
