import { animate } from "motion/react";
import { useEffect } from "react";

const FINE = "(hover: hover) and (pointer: fine)";
const REDUCE = "(prefers-reduced-motion: reduce)";
const PULL = 0.22; // share of the pointer's offset a magnetic button follows
const MAX = 5; // px
const SPRING = { type: "spring", stiffness: 320, damping: 22, mass: 0.6 } as const;

const media = (q: string) => (typeof matchMedia === "function" ? matchMedia(q) : undefined);

/**
 * Screen-wide pointer and scroll polish, one listener each (styles in index.css):
 * - the card under a mouse gets the pointer's light (--mx/--my, non-inherited, set on that card only);
 * - a `[data-magnetic]` button leans a few px toward the mouse on a spring;
 * - cards below the fold rise in once, staggered, as they enter.
 * Touch, coarse pointers and reduced motion get none of the movement; content is never
 * hidden unless an IntersectionObserver is there to show it again.
 */
export function MotionFx() {
  useEffect(() => {
    const fine = media(FINE);
    const reduce = media(REDUCE);
    let frame = 0;
    let last: PointerEvent | null = null;
    let magnet: HTMLElement | null = null;

    const release = () => {
      if (magnet) animate(magnet, { x: 0, y: 0 }, SPRING);
      magnet = null;
    };
    const tick = () => {
      frame = 0;
      const e = last;
      const target = e?.target;
      if (!e || !(target instanceof Element)) return;
      const card = target.closest<HTMLElement>(".card-glass");
      const spot = target.closest<HTMLElement>("[data-spot]");
      const m = reduce?.matches ? null : target.closest<HTMLElement>("[data-magnetic]:not(:disabled)");
      // reads first, then writes
      const cr = card?.getBoundingClientRect();
      const mr = m?.getBoundingClientRect();
      const sr = spot?.getBoundingClientRect();
      if (card && cr) {
        card.style.setProperty("--mx", `${Math.round(e.clientX - cr.left)}px`);
        card.style.setProperty("--my", `${Math.round(e.clientY - cr.top)}px`);
      }
      if (spot && sr) {
        spot.style.setProperty("--mx", `${Math.round(e.clientX - sr.left)}px`);
        spot.style.setProperty("--my", `${Math.round(e.clientY - sr.top)}px`);
      }
      if (magnet && magnet !== m) release();
      if (m && mr) {
        magnet = m;
        const clamp = (v: number) => Math.max(-MAX, Math.min(MAX, v * PULL));
        animate(m, { x: clamp(e.clientX - (mr.left + mr.width / 2)), y: clamp(e.clientY - (mr.top + mr.height / 2)) }, SPRING);
      }
    };
    const onMove = (e: PointerEvent) => {
      if (e.pointerType !== "mouse" || !fine?.matches) return;
      last = e;
      frame ||= requestAnimationFrame(tick);
    };
    document.addEventListener("pointermove", onMove, { passive: true });
    document.documentElement.addEventListener("pointerleave", release);

    // Scroll reveal
    let io: IntersectionObserver | undefined;
    let mo: MutationObserver | undefined;
    if (typeof IntersectionObserver === "function" && !reduce?.matches) {
      const observer = new IntersectionObserver(
        (entries) => {
          let n = 0;
          for (const entry of entries) {
            if (!entry.isIntersecting) continue;
            const el = entry.target as HTMLElement;
            observer.unobserve(el);
            el.style.transitionDelay = `${Math.min(n++, 5) * 60}ms`;
            requestAnimationFrame(() => el.removeAttribute("data-reveal"));
            setTimeout(() => (el.style.transitionDelay = ""), 1000);
          }
        },
        { rootMargin: "0px 0px -6% 0px" },
      );
      io = observer;
      const watch = () => {
        for (const el of document.querySelectorAll<HTMLElement>(".card-glass:not([data-revealed])")) {
          el.setAttribute("data-revealed", "");
          el.setAttribute("data-reveal", "pending");
          observer.observe(el);
        }
      };
      watch();
      // MutationObserver callbacks run before paint, so a new card never flashes in place first
      mo = new MutationObserver((records) => {
        if (records.some((r) => [...r.addedNodes].some((n) => n.nodeType === 1))) watch();
      });
      mo.observe(document.body, { childList: true, subtree: true });
    }

    return () => {
      document.removeEventListener("pointermove", onMove);
      document.documentElement.removeEventListener("pointerleave", release);
      cancelAnimationFrame(frame);
      io?.disconnect();
      mo?.disconnect();
      for (const el of document.querySelectorAll<HTMLElement>("[data-reveal]")) el.removeAttribute("data-reveal");
    };
  }, []);
  return null;
}
