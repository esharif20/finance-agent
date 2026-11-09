// Shared motion pieces for the landing page: one reduced-motion read, the magnetic pill,
// the letter roll on links and the masked line rise. Hover moves only run for a fine pointer
// that can hover; keyboard focus gets the same end state through :focus-visible in CSS.
import { motion, useMotionValue, useSpring } from "motion/react";
import { type CSSProperties, Fragment, type PointerEvent, type ReactNode, useEffect, useRef, useState } from "react";

/** Read once per mount, so a test (or a visitor) can switch it between renders. */
export function prefersReducedMotion() {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function finePointer() {
  return typeof window.matchMedia === "function" && window.matchMedia("(hover: hover) and (pointer: fine)").matches;
}

/** Phones and tablets get no WebGL on the landing page: the CSS cards stand in, and the GPU
    memory a WebGL context holds is what made iOS kill the tab on fast scrolls. */
export function liteDevice() {
  return typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse), (max-width: 899.98px)").matches;
}

export const useStill = () => useState(prefersReducedMotion)[0];

/** Smooth wheel scroll on desktop only (Lenis), stepped by GSAP's ticker so Lenis and ScrollTrigger
    run as one loop. Lenis still moves the window's native scroll, so motion's useScroll reads the
    same single source; touch keeps native momentum, and reduced motion keeps native scroll. */
export function useSmoothWheel(off: boolean) {
  useEffect(() => {
    if (off || liteDevice() || !finePointer()) return;
    let stop = () => {};
    let dead = false;
    void Promise.all([import("gsap"), import("gsap/ScrollTrigger"), import("lenis")]).then(([{ default: gsap }, { ScrollTrigger }, { default: Lenis }]) => {
      if (dead) return;
      const lenis = new Lenis({ lerp: 0.12, anchors: true, autoRaf: false });
      const tick = (t: number) => lenis.raf(t * 1000);
      lenis.on("scroll", ScrollTrigger.update);
      gsap.ticker.add(tick);
      gsap.ticker.lagSmoothing(0);
      stop = () => {
        gsap.ticker.remove(tick);
        lenis.destroy();
      };
    });
    return () => {
      dead = true;
      stop();
    };
  }, [off]);
}

/** Spec-sheet meta in a tile's corners, like the small print on a brand-guideline board.
    Decoration: the tile's own text carries the meaning, so screen readers skip it. */
export function Corners({ tl, tr, bl, br }: { tl?: ReactNode; tr?: ReactNode; bl?: ReactNode; br?: ReactNode }) {
  return (
    <span className="lp-corners" aria-hidden="true">
      {tl && <i data-c="tl">{tl}</i>}
      {tr && <i data-c="tr">{tr}</i>}
      {bl && <i data-c="bl">{bl}</i>}
      {br && <i data-c="br">{br}</i>}
    </span>
  );
}

/** Letters that roll up to a copy of themselves on hover or focus (CSS only). */
export function Roll({ text }: { text: string }) {
  return (
    <span className="lp-roll">
      <span className="lp-sr">{text}</span>
      <span aria-hidden="true" className="lp-roll-in">
        {[...text].map((ch, i) => (
          // capped so a long label still finishes in about 250ms
          <span key={i} data-c={ch === " " ? " " : ch} style={{ "--i": Math.min(i, 16) } as CSSProperties}>
            {ch === " " ? " " : ch}
          </span>
        ))}
      </span>
    </span>
  );
}

/** A pill link that leans toward the pointer and springs back (Codrops "Magnetic Buttons"). */
export function Magnetic({ href, className, children }: { href: string; className: string; children: ReactNode }) {
  const [live] = useState(() => finePointer() && !prefersReducedMotion());
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const sx = useSpring(x, { stiffness: 220, damping: 16, mass: 0.5 });
  const sy = useSpring(y, { stiffness: 220, damping: 16, mass: 0.5 });
  const move = (e: PointerEvent<HTMLAnchorElement>) => {
    if (!live || e.pointerType !== "mouse") return;
    const r = e.currentTarget.getBoundingClientRect();
    x.set((e.clientX - r.left - r.width / 2) * 0.32);
    y.set((e.clientY - r.top - r.height / 2) * 0.42);
  };
  const leave = () => {
    x.set(0);
    y.set(0);
  };
  return (
    <motion.a
      href={href}
      className={className}
      onPointerMove={move}
      onPointerLeave={leave}
      style={live ? { x: sx, y: sy } : undefined}
    >
      {children}
    </motion.a>
  );
}

const EXPO = [0.16, 1, 0.3, 1] as const;

/** Each authored line rises from behind its own baseline, once, as it enters (taste motion.md). */
export function Lines({
  lines,
  still,
  label,
  delay = 0,
  onLoad = false,
  play = true,
}: { lines: string[]; still: boolean; label?: string; delay?: number; onLoad?: boolean; play?: boolean }) {
  if (still)
    return (
      <>
        {lines.map((l, i) => (
          <Fragment key={i}>
            {i > 0 && " "}
            <span className="lp-line">{l}</span>
          </Fragment>
        ))}
      </>
    );
  const shown = { y: "0%" };
  return (
    <>
      <span className="lp-sr">{label ?? lines.join(" ")}</span>
      {lines.map((l, i) => (
        <span key={i} className="lp-line lp-line-mask" aria-hidden="true">
          <motion.span
            className="lp-line-in"
            initial={{ y: "108%" }}
            {...(onLoad ? { animate: play ? shown : undefined } : { whileInView: shown, viewport: { once: true, margin: "0px 0px -12% 0px" } })}
            transition={{ duration: 0.9, ease: EXPO, delay: delay + i * 0.11 }}
          >
            {l}
          </motion.span>
        </span>
      ))}
    </>
  );
}

/** A block that rises 24px into place once, as it enters. Never hides content without motion. */
export function Rise({ still, delay = 0, className, children }: { still: boolean; delay?: number; className?: string; children: ReactNode }) {
  if (still) return <div className={className}>{children}</div>;
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 24 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "0px 0px -10% 0px" }}
      transition={{ duration: 0.8, ease: EXPO, delay }}
    >
      {children}
    </motion.div>
  );
}

/** A surface with material: it tilts toward the pointer and lifts, a specular light and a lit
    border follow the pointer, and grain sits in the paint. Fine pointers only; keyboard focus
    inside it gets the same lifted, lit state from CSS (:focus-within). Writes the element's own
    style, so a hover never re-renders React. */
export function Material({ className = "", max = 7, children }: { className?: string; max?: number; children: ReactNode }) {
  const [live] = useState(() => finePointer() && !prefersReducedMotion());
  const move = (e: PointerEvent<HTMLDivElement>) => {
    if (e.pointerType !== "mouse") return;
    const el = e.currentTarget;
    const r = el.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width;
    const y = (e.clientY - r.top) / r.height;
    el.style.setProperty("--mx", `${(x * 100).toFixed(1)}%`);
    el.style.setProperty("--my", `${(y * 100).toFixed(1)}%`);
    // tilt toward the pointer, lift, and a magnetic pull of a few pixels after it
    el.style.transform = `perspective(900px) translate3d(${((x - 0.5) * 8).toFixed(1)}px, ${((y - 0.5) * 8 - 6).toFixed(1)}px, 0) rotateX(${((0.5 - y) * max).toFixed(2)}deg) rotateY(${((x - 0.5) * max).toFixed(2)}deg)`;
    el.dataset.hot = "";
  };
  const leave = (e: PointerEvent<HTMLDivElement>) => {
    e.currentTarget.style.transform = "";
    delete e.currentTarget.dataset.hot;
  };
  // touch has no hover: a tap lights the surface where the finger lands, then lets go
  const tap = (e: PointerEvent<HTMLDivElement>) => {
    if (e.pointerType === "mouse" || prefersReducedMotion()) return;
    const el = e.currentTarget;
    const r = el.getBoundingClientRect();
    el.style.setProperty("--mx", `${(((e.clientX - r.left) / r.width) * 100).toFixed(1)}%`);
    el.style.setProperty("--my", `${(((e.clientY - r.top) / r.height) * 100).toFixed(1)}%`);
    el.dataset.hot = "";
    window.setTimeout(() => delete el.dataset.hot, 700);
  };
  return (
    <div className={`lp-mat ${className}`} onPointerDown={tap} onPointerMove={live ? move : undefined} onPointerLeave={live ? leave : undefined}>
      {children}
      <span className="lp-mat-light" aria-hidden="true" />
    </div>
  );
}

/** Variable type that breathes in once: on load each letter sweeps from hairline to its weight in a
    wave. It holds still after that, pointer or not. Screen readers get the sentence once; the
    letters are a drawing of it. */
export function Flex({
  lines,
  label,
  still,
  play = true,
  delay = 0,
  className = "",
}: {
  lines: string[];
  label: string;
  still: boolean;
  play?: boolean;
  delay?: number;
  className?: string;
}) {
  const root = useRef<HTMLSpanElement>(null);
  let n = 0;
  if (still)
    return (
      <span className={`lp-flex ${className}`} data-still="">
        {lines.map((line, i) => (
          <Fragment key={i}>
            {i > 0 && " "}
            <span className="lp-flex-line">{line}</span>
          </Fragment>
        ))}
      </span>
    );
  return (
    <span ref={root} className={`lp-flex ${className}`} data-play={play && !still ? "" : undefined} data-still={still ? "" : undefined}>
      <span className="lp-sr">{label}</span>
      {lines.map((line, i) => (
        <span key={i} className="lp-flex-line" aria-hidden="true">
          {line.split(" ").map((w, j) => (
            <Fragment key={j}>
              {j > 0 && " "}
              <span className="lp-flex-word">
                {[...w].map((c) => {
                  const k = n++;
                  return (
                    <span key={k} data-l="" style={still ? undefined : ({ "--i": k, "--d": `${delay}s` } as CSSProperties)}>
                      {c}
                    </span>
                  );
                })}
              </span>
            </Fragment>
          ))}
        </span>
      ))}
    </span>
  );
}

/** A chapter's ground texture: /architecture's faint 22px dot grid over the chapter's flat colour,
    inked for that colour by `tone`. Static, no blobs, nothing to animate or pause. */
export function Field({ tone }: { tone: string }) {
  return <div className="lp-dots" data-tone={tone} aria-hidden="true" />;
}
