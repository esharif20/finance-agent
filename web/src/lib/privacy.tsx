import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

const KEY = "fa-private";

// the public demo starts private (docs/FRONTEND.md); a visitor's own choice is kept after that
const DEMO = import.meta.env.VITE_DEMO === "1";

const read = (): boolean => {
  try {
    const stored = localStorage.getItem(KEY);
    return stored === null ? DEMO : stored === "1";
  } catch {
    return DEMO;
  }
};

const Ctx = createContext<{ hidden: boolean; toggle: () => void }>({
  hidden: false,
  toggle: () => {},
});

export const usePrivacy = () => useContext(Ctx);

const typing = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));

/** Privacy mode: `H` or the header button blurs every `.money` and silences it for screen readers. */
export function PrivacyProvider({ children }: { children: React.ReactNode }) {
  const [hidden, setHidden] = useState(read);
  const toggle = useCallback(() => setHidden((h) => !h), []);

  useEffect(() => {
    document.documentElement.toggleAttribute("data-private", hidden);
    try {
      localStorage.setItem(KEY, hidden ? "1" : "0");
    } catch {
      /* storage blocked: the choice just won't persist */
    }
  }, [hidden]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== "h" || e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return;
      toggle();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle]);

  const value = useMemo(() => ({ hidden, toggle }), [hidden, toggle]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
