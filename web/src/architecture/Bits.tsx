/* Small effects adapted from React Bits (github.com/DavidHDev/react-bits, MIT, David Haz):
   SplitText (word reveal), CountUp and Magnet, rewritten on GSAP and trimmed to what this page
   uses. Each one renders its final state first, so a phone, reduced motion or a test sees the
   plain words and numbers; the motion is only added on a live screen. */
import { type ReactNode, useRef } from "react";
import { gsap, useGSAP } from "./smooth";

/** A heading whose words rise in once, on load. The words stay one string for screen readers. */
export function SplitWords({ text, live, as: Tag = "h1", id }: { text: string; live: boolean; as?: "h1" | "h2"; id?: string }) {
  const ref = useRef<HTMLHeadingElement>(null);
  useGSAP(
    () => {
      if (!live) return;
      gsap.from(".ar-split-w", { yPercent: 105, opacity: 0, duration: 0.9, ease: "expo.out", stagger: 0.06, delay: 0.1 });
    },
    { scope: ref, dependencies: [live] },
  );
  return (
    <Tag ref={ref} id={id} aria-label={text}>
      {text.split(" ").map((w, i) => (
        // the mask clips each word while it rises into place
        <span key={`${w}-${i}`} className="ar-split" aria-hidden="true">
          <span className="ar-split-w">{w}</span>{" "}
        </span>
      ))}
    </Tag>
  );
}

/** A whole number that counts up to its value the first time it scrolls into view. */
export function CountUp({ to, live }: { to: number; live: boolean }) {
  const ref = useRef<HTMLSpanElement>(null);
  useGSAP(
    () => {
      const el = ref.current;
      if (!live || !el || to === 0) return;
      const n = { v: 0 };
      gsap.to(n, {
        v: to,
        duration: 1.4,
        ease: "power3.out",
        scrollTrigger: { trigger: el, start: "top 85%", once: true },
        onUpdate: () => {
          el.textContent = String(Math.round(n.v));
        },
        onStart: () => {
          el.textContent = "0";
        },
      });
    },
    { scope: ref, dependencies: [live, to] },
  );
  return (
    <span ref={ref} className="ar-count">
      {to}
    </span>
  );
}

/** Pulls its child a little toward a fine pointer near it, and lets go when the pointer leaves. */
export function Magnet({ live, children }: { live: boolean; children: ReactNode }) {
  const ref = useRef<HTMLSpanElement>(null);
  useGSAP(
    (_, safe) => {
      const el = ref.current;
      if (!live || !el || !safe) return;
      const x = gsap.quickTo(el, "x", { duration: 0.5, ease: "power3.out" });
      const y = gsap.quickTo(el, "y", { duration: 0.5, ease: "power3.out" });
      let box: DOMRect | null = null;
      // read the box once per approach, never inside the move handler's hot path twice
      const enter = safe(() => {
        box = el.getBoundingClientRect();
      });
      const move = safe((e: PointerEvent) => {
        if (!box) return;
        x((e.clientX - (box.left + box.width / 2)) * 0.25);
        y((e.clientY - (box.top + box.height / 2)) * 0.35);
      });
      const leave = safe(() => {
        box = null;
        x(0);
        y(0);
      });
      el.addEventListener("pointerenter", enter);
      el.addEventListener("pointermove", move);
      el.addEventListener("pointerleave", leave);
      return () => {
        el.removeEventListener("pointerenter", enter);
        el.removeEventListener("pointermove", move);
        el.removeEventListener("pointerleave", leave);
      };
    },
    { scope: ref, dependencies: [live] },
  );
  return (
    <span ref={ref} className="ar-magnet">
      {children}
    </span>
  );
}
