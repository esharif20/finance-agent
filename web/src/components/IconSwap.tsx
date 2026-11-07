import type { ReactNode } from "react";

/** Two icons stacked in one grid cell; the outgoing one rotates and shrinks away while the
 * incoming one arrives (CSS in index.css; reduced motion crossfades). `on` shows `b`. */
export function IconSwap({ on, a, b }: { on: boolean; a: ReactNode; b: ReactNode }) {
  return (
    <span className="grid size-4 place-items-center" aria-hidden="true">
      <span className="icon-swap" data-icon={on ? "out" : "in"}>{a}</span>
      <span className="icon-swap" data-icon={on ? "in" : "out"}>{b}</span>
    </span>
  );
}
