import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

// Both builds start with values shown; a visitor's own choice is kept after that. The demo has
// its own key: it used to start private and saved that, so the old key would keep it blurred.
const KEY = import.meta.env.VITE_DEMO === "1" ? "fa-private-demo" : "fa-private";

const read = (): boolean => {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
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
