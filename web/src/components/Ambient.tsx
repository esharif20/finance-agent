import { useEffect, useState } from "react";

/** Marks <html data-scrolling> while the page scrolls, so CSS can pause decorative loops and a
    phone spends its frames on the scroll. An attribute, not state: a scroll never re-renders. */
export function useScrollPause() {
  useEffect(() => {
    const root = document.documentElement;
    let t = 0;
    const scroll = () => {
      root.setAttribute("data-scrolling", "");
      clearTimeout(t);
      t = window.setTimeout(() => root.removeAttribute("data-scrolling"), 200);
    };
    window.addEventListener("scroll", scroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", scroll);
      clearTimeout(t);
      root.removeAttribute("data-scrolling");
    };
  }, []);
}

/**
 * The drifting colour fields behind every screen (styles in index.css, `.ambient`).
 * Fixed to the viewport, so it covers the whole document at any scroll height.
 * CSS keyframes on transform only: the compositor runs them, the main thread does not.
 */
export function Ambient() {
  const [paused, setPaused] = useState(() => typeof document !== "undefined" && document.hidden);
  useScrollPause();
  useEffect(() => {
    const on = () => setPaused(document.hidden);
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, []);
  return (
    <div className="ambient" aria-hidden="true" data-paused={paused || undefined}>
      <i />
      <i />
      <i />
      <i />
    </div>
  );
}
