import { motion, useMotionValue, useSpring, useTransform } from "motion/react";
import { type CSSProperties, Fragment, type PointerEvent, useState } from "react";

export const HEADLINE = "Your money, kept at home.";
const LINE =
  "Finance Agent sorts your bank statements, flags what changed and answers questions about your spending, all on your own Mac.";

/** Read once per mount, so a test (or a visitor) can switch it between renders. */
function prefersReducedMotion() {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** #76 letter reel: each letter turns over and settles, left to right. Never used on money. */
function Reel({ text }: { text: string }) {
  let n = 0;
  const words = text.split(" ").map((word) => [...word].map((ch) => ({ ch, i: n++ })));
  return (
    <>
      <span className="lp-sr">{text}</span>
      <span aria-hidden="true">
        {words.map((letters, w) => (
          // words stay whole, so a line never breaks inside one
          <Fragment key={w}>
            <span className="lp-reel-word">
              {letters.map(({ ch, i }) => (
                // index capped at 12, so a long line still lands in about 0.6s
                <span key={i} className="lp-reel-letter" style={{ "--i": Math.min(i, 12) } as CSSProperties}>
                  {ch}
                </span>
              ))}
            </span>
            {w < words.length - 1 && " "}
          </Fragment>
        ))}
      </span>
    </>
  );
}

/** #76 word-by-word fade-up: 30ms a word. */
function Words({ text }: { text: string }) {
  return (
    <>
      <span className="lp-sr">{text}</span>
      <span aria-hidden="true">
        {text.split(" ").map((word, i) => (
          <Fragment key={i}>
            <span className="lp-word" style={{ "--w": i } as CSSProperties}>
              {word}
            </span>{" "}
          </Fragment>
        ))}
      </span>
    </>
  );
}

/** A generic card: our three-bar mark and a made-up account name, never a bank's art. */
function Card({ still }: { still: boolean }) {
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const rotateY = useSpring(useTransform(x, [-0.5, 0.5], [-10, 10]), { stiffness: 140, damping: 18 });
  const rotateX = useSpring(useTransform(y, [-0.5, 0.5], [8, -8]), { stiffness: 140, damping: 18 });
  const move = (e: PointerEvent<HTMLDivElement>) => {
    if (still || e.pointerType !== "mouse") return;
    const r = e.currentTarget.getBoundingClientRect();
    x.set((e.clientX - r.left) / r.width - 0.5);
    y.set((e.clientY - r.top) / r.height - 0.5);
  };
  const leave = () => {
    x.set(0);
    y.set(0);
  };
  return (
    <div className="lp-card-stage" onPointerMove={move} onPointerLeave={leave} aria-hidden="true">
      <motion.div className="lp-card" style={still ? undefined : { rotateX, rotateY }}>
        <div className="lp-card-face">
          <span className="lp-mark lp-mark-card">
            <i />
            <i />
            <i />
          </span>
          <span className="lp-card-chip" />
          <span className="lp-card-name">Everyday current</span>
          <span className="lp-card-num">•••• 0000</span>
        </div>
      </motion.div>
    </div>
  );
}

export function Hero() {
  const [still] = useState(prefersReducedMotion);
  return (
    <section className="lp-hero" aria-labelledby="lp-title">
      <h1 id="lp-title" className="lp-display">
        {still ? HEADLINE : <Reel text={HEADLINE} />}
      </h1>
      <div className="lp-hero-copy">
        <p className="lp-lede">{still ? LINE : <Words text={LINE} />}</p>
        <div className="lp-actions">
          <a className="lp-pill lp-pill-primary" href="/app">
            Open the demo
          </a>
          <a className="lp-pill lp-pill-ghost" href="#what">
            See what it does
          </a>
        </div>
      </div>
      <Card still={still} />
    </section>
  );
}
