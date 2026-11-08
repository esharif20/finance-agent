import { motion, useMotionValueEvent, useScroll, useSpring } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { Money } from "@/components/Money";
import { ASPECT } from "@/lib/cardArt";
import type { Pose } from "@/lib/cardPoses";
import { formatMoney } from "@/lib/money";
import type { CardScene } from "@/three/cardScene";
import { ACCOUNTS } from "./accounts.fixture";
import { finePointer, Flex, Magnetic, Roll, useStill } from "./fx";
import { digitsIn, Tally } from "./Preloader";

export const HEADLINE = "All your accounts, in one place.";
export const HEADLINE_LINES = ["All your accounts,", "in one place."];
const LINE =
  "Finance Agent brings your current account, card and savings together on your own Mac, adds them up as one, and knows a transfer between them is not spending.";

const gbp = (minor: number) => ({ minor, currency: "GBP" });
// Generic faces in the app's hues (oklch), none between 235 and 290
const HUES = [150, 28, 192];
const TOTAL = formatMoney(gbp(ACCOUNTS.balance));
const MONTH = new Date(`${ACCOUNTS.month}-01T12:00:00Z`).toLocaleString("en-GB", { month: "long", timeZone: "UTC" });

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
const ease = (v: number) => v * v * (3 - 2 * v);
const mix = (a: number, b: number, t: number) => a + (b - a) * t;
const span = (p: number, a: number, b: number) => ease(clamp01((p - a) / (b - a)));

/** The three stages of the move for card i: a fanned hand, a row with each account labelled, one
    stack over the combined view. `w` is a card's width; returns offsets from the stage centre. */
function pose(i: number, p: number, w: number, narrow: boolean, tilt: { x: number; y: number }): Pose & { label: number } {
  const n = i - 1;
  const fan = { x: narrow ? n * w * 0.16 : w * 0.62 + n * w * 0.2, y: narrow ? w * 0.62 - n * 14 : -n * 18, rz: -6 + n * 11, rx: 14, ry: -20, z: i * 34 };
  const gap = narrow ? w * 0.14 : w * 0.1;
  // on a phone the row is a column, cards on the left and each label beside its card
  const row = narrow ? { x: -w * 0.36, y: n * (w / ASPECT + gap) - w * 0.1, rz: 0, rx: 0, ry: 0, z: 0 } : { x: n * (w + gap), y: -w * 0.08, rz: 0, rx: 0, ry: 0, z: 0 };
  const stack = { x: narrow ? n * 8 : n * 14, y: narrow ? -w * 0.86 + n * 10 : -w * 0.34 + n * 12, rz: n * -2, rx: 18, ry: 0, z: i * 6 };
  const a = span(p, 0.08, 0.34);
  const b = span(p, 0.52, 0.76);
  const k = (key: keyof typeof row) => mix(mix(fan[key], row[key], a), stack[key], b);
  return {
    x: k("x"),
    y: k("y"),
    z: k("z"),
    rx: k("rx") + tilt.y * 8,
    ry: k("ry") + tilt.x * 12,
    rz: k("rz"),
    s: mix(1, narrow ? 0.82 : 0.78, b),
    layer: i,
    label: clamp01((p - 0.3) / 0.06) * (1 - clamp01((p - 0.5) / 0.06)),
  };
}

function webgl() {
  try {
    const c = document.createElement("canvas");
    return Boolean(c.getContext("webgl2") ?? c.getContext("webgl"));
  } catch {
    return false;
  }
}

function CardFace({ i, name }: { i: number; name: string }) {
  return (
    <div className="lp-acard" data-i={i} style={{ "--h": HUES[i] } as React.CSSProperties}>
      <span className="lp-card-chip" />
      <span className="lp-card-name" data-text={name} />
      <span className="lp-card-num" data-text="•••• 0000" />
    </div>
  );
}

function Combined({ landed }: { landed: number }) {
  return (
    <div className="lp-combined">
      <p className="lp-combined-label">All accounts</p>
      <Tally text={TOTAL} landed={landed} className="pl-count lp-combined-total" />
      <dl className="lp-combined-grid">
        <div>
          <dt>In, {MONTH}</dt>
          <dd>
            <Money value={gbp(ACCOUNTS.in)} />
          </dd>
        </div>
        <div>
          <dt>Out, {MONTH}</dt>
          <dd>
            <Money value={gbp(ACCOUNTS.out)} />
          </dd>
        </div>
        <div className="lp-combined-moved">
          <dt>Moved between your own accounts</dt>
          <dd>
            <Money value={gbp(ACCOUNTS.moved)} />
            <span className="lp-combined-note">recognised as transfers, so not counted as spending</span>
          </dd>
        </div>
      </dl>
    </div>
  );
}

/** The static composition: the hand of cards as a row with labels, then the combined view. Used
    under reduced motion, where nothing moves and nothing is held mid-way. */
function StillStage() {
  return (
    <div className="lp-hero-still">
      <ul className="lp-still-row">
        {ACCOUNTS.accounts.map((a, i) => (
          <li key={a.name}>
            <CardFace i={i} name={a.name} />
            <p className="lp-acct-name">{a.name}</p>
            <p className="lp-acct-bal">
              <Money value={gbp(a.balance)} />
            </p>
          </li>
        ))}
      </ul>
      <Combined landed={digitsIn(TOTAL)} />
    </div>
  );
}

/** The hero and the page's signature move. The three accounts of one made-up household arrive as
    a fanned hand of cards (WebGL, the app's own card model); scrolling lays them out in a row, each
    with its balance, then gathers them into one stack over the combined view: one total, money in
    and out across all three, and the transfers between them recognised. */
export function Hero({ delay = 0, play = true }: { delay?: number; play?: boolean }) {
  const still = useStill();
  const track = useRef<HTMLElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const cards = useRef<(HTMLDivElement | null)[]>([]);
  const labels = useRef<(HTMLDivElement | null)[]>([]);
  const [three, setThree] = useState(false);
  const [landed, setLanded] = useState(0);
  const { scrollYProgress } = useScroll({ target: track, offset: ["start start", "end end"] });
  const p = useSpring(scrollYProgress, { stiffness: 90, damping: 24, restDelta: 0.0005 });
  const [phase, setPhase] = useState(0);
  useMotionValueEvent(p, "change", (v) => setPhase(v < 0.1 ? 0 : v < 0.62 ? 1 : 2));

  // the odometer rolls up as the combined view arrives, a wheel every 70 ms
  const rolled = phase >= 2;
  useEffect(() => {
    if (still || !rolled) return;
    const ids = Array.from({ length: digitsIn(TOTAL) }, (_, d) => window.setTimeout(() => setLanded((n) => Math.max(n, d + 1)), 250 + d * 70));
    return () => ids.forEach((id) => window.clearTimeout(id));
  }, [still, rolled]);

  // one loop drives the WebGL cards, their CSS stand-ins and the labels from the same poses
  useEffect(() => {
    const el = stage.current;
    if (still || !el) return;
    let scene: CardScene | null = null;
    let raf = 0;
    let alive = true;
    let visible = true;
    const tilt = { x: 0, y: 0 };
    const aim = { x: 0, y: 0 };
    const size = () => {
      const r = el.getBoundingClientRect();
      const narrow = r.width < 720;
      const w = Math.min(narrow ? r.width * 0.5 : r.width * 0.21, 340);
      return { r, narrow, w, h: w / ASPECT };
    };
    let dims = size();
    const sizeScene = () => {
      dims = size();
      scene?.setSize(dims.w, dims.h, (dims.r.width - dims.w) / 2, (dims.r.height - dims.h) / 2);
    };
    const frame = () => {
      raf = 0;
      if (!alive || !visible) return;
      tilt.x += (aim.x - tilt.x) * 0.08;
      tilt.y += (aim.y - tilt.y) * 0.08;
      const { w, narrow } = dims;
      const poses = ACCOUNTS.accounts.map((_, i) => pose(i, p.get(), w, narrow, tilt));
      poses.forEach((q, i) => {
        const c = cards.current[i];
        if (c) c.style.transform = `translate(-50%, -50%) translate3d(${q.x}px, ${q.y}px, ${q.z}px) rotateX(${q.rx}deg) rotateY(${q.ry}deg) rotateZ(${q.rz}deg) scale(${q.s})`;
        const l = labels.current[i];
        if (l) {
          l.style.transform = narrow
            ? `translate(0, -50%) translate(${q.x + w / 2 + 14}px, ${q.y}px)`
            : `translate(-50%, 0) translate(${q.x}px, ${q.y + w / ASPECT / 2 + 14}px)`;
          l.style.opacity = String(q.label);
        }
      });
      if (scene) {
        // the scene's box is one card at the stage centre: offsets are from its top-left
        scene.update(
          poses.map((q) => ({ ...q, x: q.x, y: q.y })),
          { x: dims.w / 2 + tilt.x * 260, y: dims.h / 2 - tilt.y * 200, on: finePointer() ? 1 : 0.4 },
          poses.map(() => null),
        );
        scene.render();
      }
      raf = requestAnimationFrame(frame);
    };
    const kick = () => {
      if (!raf) raf = requestAnimationFrame(frame);
    };
    const io = new IntersectionObserver(([e]) => {
      visible = Boolean(e?.isIntersecting);
      if (visible) kick();
    });
    io.observe(el);
    const move = (e: PointerEvent) => {
      if (e.pointerType !== "mouse") return;
      aim.x = e.clientX / window.innerWidth - 0.5;
      aim.y = e.clientY / window.innerHeight - 0.5;
    };
    const resize = () => sizeScene();
    window.addEventListener("pointermove", move);
    window.addEventListener("resize", resize);
    if (canvas.current && webgl()) {
      import("@/three/cardScene")
        .then(({ createCardScene }) => {
          if (!alive || !canvas.current) return;
          scene = createCardScene(canvas.current, 1600);
          sizeScene();
          scene.setFaces(
            ACCOUNTS.accounts.map((_, i) => ({ art: null, hue: HUES[i] ?? null, h: dims.h })),
            "#000000",
          );
          setThree(true);
        })
        .catch(() => setThree(false)); // the CSS cards stay
    }
    kick();
    return () => {
      alive = false;
      cancelAnimationFrame(raf);
      io.disconnect();
      window.removeEventListener("pointermove", move);
      window.removeEventListener("resize", resize);
      scene?.dispose();
    };
  }, [still, p]);

  return (
    <section ref={track} className="lp-hero" data-still={still ? "" : undefined} aria-labelledby="lp-title">
      <div className="lp-hero-pin">
        <div className="lp-mesh" aria-hidden="true" />
        <div className="lp-grain" aria-hidden="true" />
        <div className="lp-hero-copy" data-phase={phase}>
          <h1 id="lp-title" className="lp-display">
            <Flex lines={HEADLINE_LINES} label={HEADLINE} still={still} play={play} delay={delay} />
          </h1>
          <motion.div
            className="lp-hero-foot"
            initial={still ? false : { opacity: 0, y: 16 }}
            animate={play ? { opacity: 1, y: 0 } : undefined}
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
        </div>
        {still ? (
          <StillStage />
        ) : (
          <div ref={stage} className="lp-stage" data-three={three ? "" : undefined} data-phase={phase} data-play={play ? "" : undefined} aria-hidden="true">
            <canvas ref={canvas} className="lp-stage-canvas" />
            {ACCOUNTS.accounts.map((a, i) => (
              <div key={a.name} ref={(n) => void (cards.current[i] = n)} className="lp-acard-slot">
                <CardFace i={i} name={a.name} />
              </div>
            ))}
            {ACCOUNTS.accounts.map((a, i) => (
              <div key={a.name} ref={(n) => void (labels.current[i] = n)} className="lp-acct">
                <p className="lp-acct-name">{a.name}</p>
                <p className="lp-acct-bal">
                  <Money value={gbp(a.balance)} />
                </p>
                <p className="lp-acct-flow">
                  <span>
                    In <Money value={gbp(a.in)} />
                  </span>
                  <span>
                    Out <Money value={gbp(a.out)} />
                  </span>
                </p>
              </div>
            ))}
            <Combined landed={landed} />
          </div>
        )}
        <p className="lp-sr">
          {ACCOUNTS.accounts.map((a) => `${a.name}: ${formatMoney(gbp(a.balance))}.`).join(" ")} Together: {TOTAL}.
        </p>
      </div>
    </section>
  );
}
