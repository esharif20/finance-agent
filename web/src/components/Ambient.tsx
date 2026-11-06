import { useEffect, useState } from "react";

/**
 * The drifting colour fields behind every screen (styles in index.css, `.ambient`).
 * Fixed to the viewport, so it covers the whole document at any scroll height.
 * CSS keyframes on transform only: the compositor runs them, the main thread does not.
 */
export function Ambient() {
  const [paused, setPaused] = useState(() => typeof document !== "undefined" && document.hidden);
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
