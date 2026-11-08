import { useState } from "react";
import { Gateway } from "./Gateway";
import { Pile } from "./Pile";
import { Principles } from "./Principles";
import "./story.css";

/** Read once per mount, like the hero, so a test (or a visitor) can switch it between renders. */
function prefersReducedMotion() {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** #300: the scroll story between the hero and the call to action. Native scroll only; no hijacking. */
export function Story() {
  const [still] = useState(prefersReducedMotion);
  return (
    <div className="st" data-still={still ? "" : undefined}>
      <Pile still={still} />
      <Gateway still={still} />
      <Principles still={still} />
    </div>
  );
}
