import { motion, useMotionValueEvent, useScroll, useSpring } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { Money } from "@/components/Money";
import { ASPECT } from "@/lib/cardArt";
import type { Pose } from "@/lib/cardPoses";
import { formatMoney } from "@/lib/money";
import type { CardScene } from "@/three/cardScene";
import { ACCOUNTS } from "./accounts.fixture";
import { Field, finePointer, Flex, liteDevice, Magnetic, Roll, useStill } from "./fx";
import { digitsIn, Tally } from "./Preloader";
import { OVERVIEW, REVIEW } from "./story/story.fixture";
import "./hero.css";

export const HEADLINE = "An AI agent for your money, on your own Mac.";
export const HEADLINE_LINES = ["An AI agent for your money,", "on your own Mac."];
const LINE =
  "It watches every account, spots what changed, answers with figures it has checked, and learns what you care about. It suggests; you decide. Nothing leaves your Mac unless you say so.";

// What makes it an agent; every line is something the app does today
const CAPS = [
  { name: "Watches", tip: "Syncs in the background and spots a new subscription from its first charge." },
  { name: "Plans", tip: "Works out which lookups a question needs before any model is asked." },
  { name: "Checks", tip: "Every figure in a reply must match your ledger, or it isn’t shown." },
  { name: "Remembers", tip: "Keeps what you tell it to remember, where you can read and delete it." },
  { name: "Learns", tip: "Ranks Insights by what you mark useful, not by what you click." },
  { name: "Suggests", tip: "Proposes a change and waits for you to press Confirm. It never moves money." },
];

const gbp = (minor: number) => ({ minor, currency: "GBP" });
// Generic faces in the app's hues (oklch), none between 235 and 290
const HUES = [150, 28, 192];
const TOTAL = formatMoney(gbp(ACCOUNTS.balance));
const MONTH = new Date(`${ACCOUNTS.month}-01T12:00:00Z`).toLocaleString("en-GB", { month: "long", timeZone: "UTC" });

// the ledger behind the cards: rows from the same made-up month, on a plane of their own
const ROWS = [...REVIEW.map((r) => ({ name: r.merchant, amount: r.amount })), ...OVERVIEW.top.map((c) => ({ name: c.name, amount: c.amount }))];

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
    const gl = c.getContext("webgl2") ?? c.getContext("webgl");
    gl?.getExtension("WEBGL_lose_context")?.loseContext(); // the probe gives its context back at once
    return Boolean(gl);
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

/** The ledger field: a fine grid of dots behind the hero that the pointer pulls like iron filings.
    Dots near the cursor lift, brighten and briefly turn into digits that scramble, then settle as a
    tally does. One 2D canvas, DPR capped at 1.5, drawn only while something moves and paused off
    screen. Desktop with a fine pointer only: on touch or under 900px the canvas is never created. */
function LedgerField() {
  const ref = useRef<HTMLCanvasElement>(null);
  const [on] = useState(() => finePointer() && !liteDevice());
  useEffect(() => {
    const cv = ref.current;
    const ctx = cv?.getContext("2d");
    if (!on || !cv || !ctx) return;
    const GAP = 22;
    const R = 170; // the field's reach, px
    let w = 0;
    let h = 0;
    let dpr = 1;
    let cols = 0;
    let rows = 0;
    let rect = cv.getBoundingClientRect();
    const size = () => {
      rect = cv.getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      w = rect.width;
      h = rect.height;
      cv.width = Math.round(w * dpr);
      cv.height = Math.round(h * dpr);
      cols = Math.ceil(w / GAP) + 1;
      rows = Math.ceil(h / GAP) + 1;
    };
    size();
    // the pointer, eased (lerp 0.1), and an energy that rises as it moves and decays when it rests
    const aim = { x: -1e4, y: -1e4 };
    const at = { x: -1e4, y: -1e4 };
    let energy = 0;
    let raf = 0;
    let visible = true;
    const draw = (now: number) => {
      raf = 0;
      if (!visible) return;
      if (at.x < -1e3) {
        at.x = aim.x;
        at.y = aim.y;
      }
      at.x += (aim.x - at.x) * 0.1;
      at.y += (aim.y - at.y) * 0.1;
      energy *= 0.965;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      ctx.font = "600 10px ui-sans-serif, system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      const tick = Math.floor(now / 90);
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          const bx = c * GAP + (r % 2 ? GAP / 2 : 0);
          const by = r * GAP;
          const dx = bx - at.x;
          const dy = by - at.y;
          const d = Math.hypot(dx, dy);
          const f = d < R ? (1 - d / R) ** 2 * energy : 0;
          if (f < 0.01) {
            ctx.fillStyle = "rgba(255,255,255,0.07)";
            ctx.fillRect(bx - 0.75, by - 0.75, 1.5, 1.5);
            continue;
          }
          // filings: pushed out along the radius with a ripple that travels outwards
          const push = f * (10 + 6 * Math.sin(d / 16 - now / 140));
          const x = bx + (dx / (d || 1)) * push;
          const y = by + (dy / (d || 1)) * push - f * 6;
          if (f > 0.32) {
            // close in: a digit that scrambles, slower as the energy falls, so it settles
            const n = (c * 7 + r * 13 + Math.floor(tick * (0.4 + energy))) % 10;
            ctx.fillStyle = `rgba(154,225,135,${Math.min(0.85, f * 1.1).toFixed(3)})`;
            ctx.fillText(String(n), x, y);
          } else {
            const s = 1.5 + f * 2.5;
            ctx.fillStyle = `rgba(${Math.round(255 - 100 * f)},${Math.round(255 - 30 * f)},${Math.round(255 - 120 * f)},${(0.07 + f * 0.9).toFixed(3)})`;
            ctx.fillRect(x - s / 2, y - s / 2, s, s);
          }
        }
      }
      if (energy > 0.004) raf = requestAnimationFrame(draw);
    };
    const kick = () => {
      if (!raf && visible) raf = requestAnimationFrame(draw);
    };
    const move = (e: PointerEvent) => {
      if (e.pointerType !== "mouse") return;
      aim.x = e.clientX - rect.left;
      aim.y = e.clientY - rect.top;
      energy = Math.min(1, energy + 0.08);
      kick();
    };
    const reflow = () => {
      size();
      kick();
    };
    const resync = () => void (rect = cv.getBoundingClientRect());
    const io = new IntersectionObserver(([e]) => {
      visible = Boolean(e?.isIntersecting);
      if (visible) kick();
    });
    io.observe(cv);
    window.addEventListener("pointermove", move);
    window.addEventListener("resize", reflow);
    window.addEventListener("scroll", resync, { passive: true });
    energy = 0.001;
    kick(); // one frame for the resting grid
    return () => {
      cancelAnimationFrame(raf);
      io.disconnect();
      window.removeEventListener("pointermove", move);
      window.removeEventListener("resize", reflow);
      window.removeEventListener("scroll", resync);
    };
  }, [on]);
  return on ? <canvas ref={ref} className="lp-ledger-field" aria-hidden="true" /> : null;
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
  const rows = useRef<HTMLUListElement>(null);
  const cards = useRef<(HTMLDivElement | null)[]>([]);
  const labels = useRef<(HTMLDivElement | null)[]>([]);
  const floors = useRef<(HTMLSpanElement | null)[]>([]);
  const backs = useRef<(HTMLSpanElement | null)[]>([]);
  const sheens = useRef<(HTMLSpanElement | null)[]>([]);
  const [three, setThree] = useState(false);
  const playing = useRef(play);
  playing.current = play;
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
    // the deal: on load the cards rise from below the fold, fan out one after another, and a
    // light sweeps across them; it starts when the tally hands over (playing.current)
    let dealt = 0;
    const aim = { x: 0, y: 0 };
    const size = () => {
      const r = el.getBoundingClientRect();
      const narrow = r.width < 720;
      // a bigger hand on desktop; hero.css sizes the slots to match (min(28vw, 440px))
      const w = Math.min(narrow ? r.width * 0.5 : r.width * 0.28, 440);
      return { r, narrow, w, h: w / ASPECT };
    };
    let dims = size();
    // render on demand: the loop runs only while something moves, and sleeps when the scroll,
    // the pointer tilt and the deal have all settled
    let lastP = -1;
    let drawn = false;
    const sizeScene = () => {
      dims = size();
      scene?.setSize(dims.w, dims.h, (dims.r.width - dims.w) / 2, (dims.r.height - dims.h) / 2);
    };
    const frame = () => {
      raf = 0;
      if (!alive || !visible) return;
      tilt.x += (aim.x - tilt.x) * 0.08;
      tilt.y += (aim.y - tilt.y) * 0.08;
      const settling = Math.abs(aim.x - tilt.x) + Math.abs(aim.y - tilt.y) > 0.0005;
      const { w, narrow } = dims;
      const now = performance.now();
      if (playing.current && !dealt) dealt = now;
      // the deal waits out the preloader's sheet as it lifts off the top of the page
      const since = dealt ? now - dealt - 450 : 0;
      const pv = p.get();
      // on idle the fan turns slowly in place, desktop and fine pointer only, and only while it is a fan
      const sway = finePointer() && !narrow ? clamp01((since - 2200) / 1800) * (1 - clamp01((pv - 0.02) / 0.08)) : 0;
      const poses = ACCOUNTS.accounts.map((_, i) => {
        const q0 = pose(i, pv, w, narrow, tilt);
        // magnetic depth: each card sits at its own depth, so the eased tilt shifts the front card most (2 to 6 px)
        const d = 4 + 4 * i;
        const q1 = { ...q0, x: q0.x + tilt.x * d, y: q0.y + tilt.y * d };
        const q = sway ? { ...q1, ry: q1.ry + Math.sin(now / 2600) * 9 * sway, rz: q1.rz + Math.sin(now / 3400 + i * 1.3) * 2.2 * sway, y: q1.y + Math.sin(now / 2100 + i) * 6 * sway } : q1;
        // a dealt card: thrown up from below and out of depth, spinning flat as it lands (quartic ease-out)
        const k = 1 - (1 - clamp01((since - 160 - i * 170) / 1400)) ** 4;
        const u = 1 - k;
        return { ...q, rest: q.y, deal: k, y: q.y + u * w * 2.1, z: q.z - u * 520, rz: q.rz * k + u * (14 - i * 14), rx: q.rx + u * 64, ry: q.ry + u * (i - 1) * 18, s: q.s * (0.78 + 0.22 * k) };
      });
      // depth field: the ledger plane drifts up slower than the cards and sinks back as they merge
      if (rows.current) rows.current.style.transform = `translate3d(${tilt.x * -40}px, ${-pv * 22 + tilt.y * -24}vh, ${-260 - pv * 200}px)`;
      const sweep = clamp01((since - 1100) / 1500);
      const sweeping = sweep > 0 && sweep < 1;
      // contact shadows on the floor under the fan; they gather as each card lands, gone once the hand opens
      const onFloor = 1 - clamp01((pv - 0.04) / 0.16);
      poses.forEach((q, i) => {
        const f = floors.current[i];
        if (f) {
          f.style.transform = `translate(-50%, -50%) translate3d(${q.x}px, ${q.rest + dims.h * 0.78}px, 0) scale(${(1.25 - 0.25 * q.deal) * q.s}, ${0.22 * q.s})`;
          f.style.opacity = String(q.deal * onFloor);
        }
        // edge light: a soft backlight that spills round each card's rim from behind
        const bk = backs.current[i];
        if (bk) {
          bk.style.transform = `translate(-50%, -50%) translate3d(${q.x}px, ${q.y}px, 0) rotate(${q.rz}deg) scale(${q.s})`;
          bk.style.opacity = String(q.deal);
        }
        // a sheen slides across the face in step with the tilt, opposite the pointer like a real reflection
        const sh = sheens.current[i];
        if (sh) sh.style.transform = `translate3d(${(-tilt.x * 150).toFixed(1)}%, ${(-tilt.y * 50).toFixed(1)}%, 0) skewX(-14deg)`;
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
          sweeping
            ? { x: mix(-dims.r.width * 0.4, dims.r.width * 0.9, ease(sweep)), y: dims.h * 0.2, on: 1.8 }
            : { x: dims.w / 2 + tilt.x * 260, y: dims.h / 2 - tilt.y * 200, on: finePointer() ? 1 : 0.4 },
          poses.map(() => null),
        );
        scene.render();
        if (!drawn) {
          drawn = true;
          el.dataset.drawn = ""; // the canvas fades in over the CSS cards only once it has a frame
        }
      }
      // the last card lands at 160 + 2 * 170 + 1400 ms; the WebGL sweep ends at 1100 + 1500 ms
      const dealing = !dealt || since < 2800;
      const idling = sway > 0;
      const moving = Math.abs(pv - lastP) > 0.00005;
      lastP = pv;
      if (dealing || idling || moving || settling || (scene && !drawn)) raf = requestAnimationFrame(frame);
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
      if (!el.dataset.lit) el.dataset.lit = "";
      aim.x = e.clientX / window.innerWidth - 0.5;
      aim.y = e.clientY / window.innerHeight - 0.5;
      kick();
    };
    const resize = () => {
      sizeScene();
      kick();
    };
    const offP = p.on("change", kick);
    window.addEventListener("pointermove", move);
    window.addEventListener("resize", resize);
    if (canvas.current && !liteDevice() && webgl()) {
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
          kick();
        })
        .catch(() => setThree(false)); // the CSS cards stay
    }
    kick();
    return () => {
      alive = false;
      offP();
      cancelAnimationFrame(raf);
      io.disconnect();
      window.removeEventListener("pointermove", move);
      window.removeEventListener("resize", resize);
      scene?.dispose();
    };
  }, [still, p, playing]);

  return (
    <section ref={track} className="lp-hero" data-still={still ? "" : undefined} aria-labelledby="lp-title">
      <div className="lp-hero-pin">
        <Field tone="night" />
        {still ? <span className="lp-ledger-still" aria-hidden="true" /> : <LedgerField />}
        <div className="lp-halo" aria-hidden="true" data-phase={phase} />
        <div className="lp-grain" aria-hidden="true" />
        <div className="lp-hero-copy" data-phase={phase}>
          <h1 id="lp-title" className="lp-display" style={{ "--hd": `${delay}s` } as React.CSSProperties}>
            <Flex lines={HEADLINE_LINES} label={HEADLINE} still={still} play={play} delay={delay} />
          </h1>
          <motion.div
            className="lp-hero-foot"
            initial={still ? false : { opacity: 0, y: 16 }}
            animate={play ? { opacity: 1, y: 0 } : undefined}
            transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1], delay: delay + 0.55 }}
          >
            <p className="lp-lede">{LINE}</p>
            <ul className="lp-caps" aria-label="What it does on its own">
              {CAPS.map((c) => (
                <li key={c.name} tabIndex={0}>
                  <span className="lp-cap-name">{c.name}</span>
                  <span className="lp-cap-tip">{c.tip}</span>
                </li>
              ))}
            </ul>
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
            <ul ref={rows} className="lp-rows">
              {ROWS.map((r, i) => (
                <li key={r.name} style={{ "--i": i } as React.CSSProperties}>
                  <span>{r.name}</span>
                  <Money value={r.amount} />
                </li>
              ))}
            </ul>
            {ACCOUNTS.accounts.map((a, i) => (
              <span key={a.name} ref={(n) => void (floors.current[i] = n)} className="lp-acard-floor" style={{ "--h": HUES[i] } as React.CSSProperties} />
            ))}
            {ACCOUNTS.accounts.map((a, i) => (
              <span key={a.name} ref={(n) => void (backs.current[i] = n)} className="lp-acard-back" style={{ "--h": HUES[i] } as React.CSSProperties} />
            ))}
            <canvas ref={canvas} className="lp-stage-canvas" />
            {ACCOUNTS.accounts.map((a, i) => (
              <div key={a.name} ref={(n) => void (cards.current[i] = n)} className="lp-acard-slot" style={{ "--i": i } as React.CSSProperties}>
                <CardFace i={i} name={a.name} />
                <span className="lp-acard-light">
                  <span ref={(n) => void (sheens.current[i] = n)} className="lp-acard-sheen" />
                </span>
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
