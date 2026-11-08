import {
  animate,
  motion,
  type MotionValue,
  useMotionTemplate,
  useMotionValue,
  useScroll,
  useSpring,
  useTransform,
} from "motion/react";
import { type PointerEvent, useEffect, useRef, useState } from "react";
import { Money } from "@/components/Money";
import { finePointer, Lines, Magnetic, Roll, useStill } from "./fx";
import { REVIEW } from "./story/story.fixture";

export const HEADLINE = "Your money, kept at home.";
export const HEADLINE_LINES = ["Your money,", "kept at home."];
const LINE =
  "Finance Agent sorts your bank statements, flags what changed and answers questions about your spending, all on your own Mac.";

/** A generic card: our three-bar mark and a made-up account name, never a bank's art. The light
    follows the pointer like a lamp over a metal card. */
function Card({ still, sink }: { still: boolean; sink: MotionValue<number> }) {
  const [live] = useState(() => !still && finePointer());
  const px = useMotionValue(0.5);
  const py = useMotionValue(0.3);
  const rotateY = useSpring(useTransform(px, [0, 1], [-14, 14]), { stiffness: 150, damping: 18 });
  const rotateX = useSpring(useTransform(py, [0, 1], [10, -10]), { stiffness: 150, damping: 18 });
  const gx = useSpring(useTransform(px, [0, 1], [0, 100]), { stiffness: 120, damping: 20 });
  const gy = useSpring(useTransform(py, [0, 1], [0, 100]), { stiffness: 120, damping: 20 });
  // specular highlight: a material light on the card, not page decoration
  const glare = useMotionTemplate`radial-gradient(circle at ${gx}% ${gy}%, rgb(255 255 255 / 0.55), rgb(255 255 255 / 0) 46%)`;
  // the card leans back and sinks a little as the water rises on scroll
  const tilt = useTransform(sink, [0, 1], [0, 18]);
  const drop = useTransform(sink, [0, 1], ["0%", "38%"]);
  const move = (e: PointerEvent<HTMLDivElement>) => {
    if (!live || e.pointerType !== "mouse") return;
    const r = e.currentTarget.getBoundingClientRect();
    px.set((e.clientX - r.left) / r.width);
    py.set((e.clientY - r.top) / r.height);
  };
  const leave = () => {
    px.set(0.5);
    py.set(0.3);
  };
  const face = (
    <div className="lp-card-face" data-ui="">
      <span className="lp-mark lp-mark-card">
        <i />
        <i />
        <i />
      </span>
      <span className="lp-card-chip" />
      {/* printed on the card, drawn by CSS so it is not read as a label over the headline */}
      <span className="lp-card-name" data-text="Everyday current" />
      <span className="lp-card-num" data-text="•••• 0000" />
      {live && <motion.span className="lp-card-glare" style={{ backgroundImage: glare }} />}
    </div>
  );
  return (
    <motion.div className="lp-card-stage" style={still ? undefined : { y: drop, rotateX: tilt }} aria-hidden="true">
      <div className="lp-card-hit" onPointerMove={move} onPointerLeave={leave}>
        {still ? <div className="lp-card">{face}</div> : <motion.div className="lp-card" style={{ rotateX, rotateY }}>{face}</motion.div>}
      </div>
    </motion.div>
  );
}

/** The waterline hero: paper above, mint below, and the headline changes ink where it crosses. On
    load the water rises to the seam; on scroll it keeps rising, and the card goes under. */
export function Hero({ delay = 0 }: { delay?: number }) {
  const still = useStill();
  const ref = useRef<HTMLElement>(null);
  const [rest] = useState(() => (typeof window.matchMedia === "function" && window.matchMedia("(max-width: 720px)").matches ? 50 : 56));
  const rise = useMotionValue(still ? 1 : 0);
  useEffect(() => {
    if (still) return;
    const a = animate(rise, 1, { duration: 1.1, ease: [0.16, 1, 0.3, 1], delay });
    return () => a.stop();
  }, [still, delay, rise]);
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start start", "end start"] });
  const lift = useSpring(scrollYProgress, { stiffness: 90, damping: 24, restDelta: 0.0005 });
  // seam in % of the hero, 100 = no water; rises to `rest` on load, then on toward the top on scroll
  const seam = useTransform([rise, lift], ([r = 0, p = 0]: number[]) => 100 - r * (100 - rest) - p * (rest - 6));
  const below = useMotionTemplate`inset(${seam}% 0 0 0)`;
  const above = useTransform(seam, (s) => `inset(0 0 ${100 - s}% 0)`);
  const waterY = useTransform(seam, (s) => `${s}%`);
  const typeY = useTransform(lift, [0, 1], ["0%", "-18%"]);
  // the atmosphere plane: statement lines seen through the water, drifting faster than the type
  const rowsY = useTransform(lift, [0, 1], ["0%", "-70%"]);
  const rows = (
    <motion.ul className="lp-rows" style={still ? undefined : { y: rowsY }} aria-hidden="true">
      {REVIEW.map((r) => (
        <li key={r.merchant}>
          <span>{r.merchant}</span>
          <Money value={r.amount} />
        </li>
      ))}
    </motion.ul>
  );

  const head = (copy: boolean) => (
    <motion.div className="lp-hero-type" style={still ? undefined : { y: typeY }}>
      {copy ? (
        <p className="lp-display" aria-hidden="true">
          <Lines lines={HEADLINE_LINES} still={still} delay={delay + 0.1} onLoad />
        </p>
      ) : (
        <h1 id="lp-title" className="lp-display">
          <Lines lines={HEADLINE_LINES} label={HEADLINE} still={still} delay={delay + 0.1} onLoad />
        </h1>
      )}
    </motion.div>
  );

  return (
    <section ref={ref} className="lp-hero" aria-labelledby="lp-title" style={{ "--rest": `${rest}%` } as React.CSSProperties}>
      {still ? (
        <>
          <div className="lp-water" />
          {rows}
          <div className="lp-plane lp-plane-above">{head(false)}</div>
          <Card still sink={lift} />
          <div className="lp-water lp-water-front" />
          <div className="lp-plane lp-plane-below">{head(true)}</div>
        </>
      ) : (
        <>
          <motion.div className="lp-water" style={{ y: waterY }} />
          <motion.div className="lp-plane lp-plane-rows" style={{ clipPath: below }}>
            {rows}
          </motion.div>
          <motion.div className="lp-plane lp-plane-above" style={{ clipPath: above }}>
            {head(false)}
          </motion.div>
          <Card still={false} sink={lift} />
          <motion.div className="lp-water lp-water-front" style={{ y: waterY }} />
          <motion.div className="lp-plane lp-plane-below" style={{ clipPath: below }}>
            {head(true)}
          </motion.div>
        </>
      )}
      <motion.div
        className="lp-hero-foot"
        initial={still ? false : { opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1], delay: delay + 0.55 }}
      >
        <p className="lp-lede">{LINE}</p>
        <div className="lp-actions">
          <Magnetic href="/app" className="lp-pill lp-pill-primary">
            <Roll text="Open the demo" />
          </Magnetic>
          <a className="lp-link" href="#what">
            <Roll text="See what it does" />
          </a>
        </div>
      </motion.div>
    </section>
  );
}
