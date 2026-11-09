import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useMotionValue, useMotionValueEvent } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { Money } from "@/components/Money";
import { ASPECT } from "@/lib/cardArt";
import type { Pose } from "@/lib/cardPoses";
import { formatMoney } from "@/lib/money";
import type { CardScene } from "@/three/cardScene";
import { ACCOUNTS } from "./accounts.fixture";
import { CHIPS, SPLITS } from "./hero.fixture";
import { finePointer, Flex, liteDevice, Magnetic, Roll, useStill } from "./fx";
import { digitsIn, Tally } from "./Preloader";
import "./hero.css";

export const HEADLINE = "An AI agent for your money, on your own Mac.";
export const HEADLINE_LINES = ["An AI agent for your money,", "on your own Mac."];
const LINE =
  "It watches every account, spots what changed, answers with figures it has checked, and learns what you care about. It suggests; you decide. Nothing leaves your Mac unless you say so.";


const gbp = (minor: number) => ({ minor, currency: "GBP" });
// Generic faces in the app's hues (oklch), none between 235 and 290
const HUES = [150, 28, 192];
const TOTAL = formatMoney(gbp(ACCOUNTS.balance));
const MONTH = new Date(`${ACCOUNTS.month}-01T12:00:00Z`).toLocaleString("en-GB", { month: "long", timeZone: "UTC" });

/** The hero's three beats, each a statement that scrolls past the pinned cards: the hand is dealt,
    laid out as one ledger, then added up. The panel's meta row names the stage in words. */
const BEATS = [
  { stage: "Dealt from three accounts" },
  {
    stage: "Laid out as one ledger",
    title: "Every account, read into one ledger.",
    body: "Balances, money in and money out for each account, synced in the background and checked against what came before.",
  },
  {
    stage: "Added up and checked",
    title: "One total, worked out by code, not guessed.",
    body: "Transfers between your own accounts are paired and kept out of spending, so nothing counts twice.",
  },
];

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
/** One made-up row per card, worn by the card it came from: it rides the card through the deal and
    the row, then is read into it as the hand gathers. */
const WORN = [
  { merchant: CHIPS[0]!.merchant, amount: CHIPS[0]!.amount, note: CHIPS[0]!.category[1], ok: true, at: "l" },
  { merchant: CHIPS[2]!.merchant, amount: CHIPS[2]!.amount, note: "To review", ok: false, at: "c" },
  { merchant: "Transfer paired", amount: null, note: "not spending", ok: true, at: "r" },
];
const ease = (v: number) => v * v * (3 - 2 * v);
const mix = (a: number, b: number, t: number) => a + (b - a) * t;
const span = (p: number, a: number, b: number) => ease(clamp01((p - a) / (b - a)));

/** The three stages of the move for card i: a fanned hand, a row with each account labelled, one
    stack over the combined view. `w` is a card's width; returns offsets from the stage centre. */
function pose(i: number, p: number, w: number, narrow: boolean, tilt: { x: number; y: number }, ox = 0): Pose & { label: number } {
  const n = i - 1;
  // desktop: the whole move happens right of the copy column (ox); a phone keeps it centred
  const fan = { x: ox + (narrow ? n * w * 0.16 : n * w * 0.44), y: narrow ? -w * 0.1 - n * 14 : -w * 0.12 - n * 22, rz: -6 + n * 11, rx: 14, ry: -20, z: i * 34 };
  const gap = narrow ? w * 0.14 : w * 0.1;
  // on a phone the row is a column, cards on the left and each label beside its card
  const row = narrow ? { x: -w * 0.42, y: n * (w / ASPECT + gap) - w * 0.1, rz: 0, rx: 0, ry: 0, z: 0 } : { x: ox + n * (w + gap), y: w * 0.02, rz: 0, rx: 0, ry: 0, z: 0 };
  const stack = { x: ox + (narrow ? n * 8 : n * 14), y: narrow ? -w * 0.55 + n * 10 : -w * 0.62 + n * 12, rz: n * -2, rx: 18, ry: 0, z: i * 6 };
  // even pace: deal out 0.04 to 0.28, the labelled row drifts toward you to 0.5, gather to 0.72,
  // then the totals hold while the stack rises; no stretch of scroll leaves the frame still
  const a = span(p, 0.04, 0.28);
  const drift = span(p, 0.26, 0.52);
  const b = span(p, 0.5, 0.72);
  const rise = span(p, 0.72, 1);
  const k = (key: keyof typeof row) => mix(mix(fan[key], row[key], a), stack[key], b);
  return {
    x: k("x"),
    y: k("y") - w * 0.05 * drift * (1 - b) - w * 0.08 * rise,
    z: k("z"),
    rx: k("rx") + tilt.y * 8,
    // gathering, each card turns right over once, back and all, one after the other
    ry: k("ry") + tilt.x * 12 + (narrow ? 0 : 360 * span(p, 0.48 + i * 0.05, 0.66 + i * 0.05)),
    rz: k("rz"),
    s: mix(mix(narrow ? 1.1 : 1.45, 1 + (narrow ? 0 : 0.06 * drift), a), narrow ? 0.82 : 1, b),
    layer: i,
    label: clamp01((p - 0.2) / 0.06) * (1 - clamp01((p - 0.47) / 0.05)),
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

function Combined({ landed, innerRef }: { landed: number; innerRef?: React.Ref<HTMLDivElement> }) {
  return (
    <div ref={innerRef} className="lp-combined">
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
      if (stale) {
        stale = false;
        rect = cv.getBoundingClientRect();
      }
      aim.x = e.clientX - rect.left;
      aim.y = e.clientY - rect.top;
      energy = Math.min(1, energy + 0.08);
      kick();
    };
    const reflow = () => {
      size();
      kick();
    };
    let stale = false;
    const resync = () => void (stale = true);
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
  const cards = useRef<(HTMLDivElement | null)[]>([]);
  const labels = useRef<(HTMLDivElement | null)[]>([]);
  const floors = useRef<(HTMLSpanElement | null)[]>([]);
  const backs = useRef<(HTMLSpanElement | null)[]>([]);
  const sheens = useRef<(HTMLSpanElement | null)[]>([]);
  const peek = useRef<HTMLDivElement>(null);
  const worn = useRef<(HTMLSpanElement | null)[]>([]);
  const combined = useRef<HTMLDivElement>(null);
  const [three, setThree] = useState(false);
  const playing = useRef(play);
  playing.current = play;
  const [landed, setLanded] = useState(0);
  const [lite] = useState(liteDevice);
  // one scroll driver: GSAP ScrollTrigger (stepped by Lenis's tick on desktop) writes the raw
  // progress through the hero to `goal`; the frame loop below moves a playhead `p` after it (see
  // PLAYHEAD), so every stage plays in full however fast the scroll
  const goal = useRef(0);
  const kicker = useRef<(() => void) | null>(null);
  const p = useMotionValue(0);
  useGSAP(
    () => {
      if (still) return;
      gsap.registerPlugin(ScrollTrigger); // here, not at import: it reads matchMedia on register
      // the cards' run ends one screen early: the last screen of the track is the hand-over, where
      // the next section rises over the frame as one move (no cut)
      const st = ScrollTrigger.create({
        trigger: track.current,
        start: "top top",
        end: "bottom 200%",
        onUpdate: (self) => {
          goal.current = self.progress;
          kicker.current?.();
        },
      });
      // the light: each card's hue pools on the dark under it and follows the move, scrubbed on the
      // same run (scrub 0.35 s, about the playhead's own lag); transform and opacity only
      const light = gsap.timeline({
        defaults: { ease: "none" },
        scrollTrigger: { trigger: track.current, start: "top top", end: "bottom 200%", scrub: 0.35 },
      });
      light
        .fromTo(".lp-hero-rail i", { scaleX: 0.04 }, { scaleX: 1, duration: 1 }, 0)
        .to(".lp-wash-0", { xPercent: -30, yPercent: 6, duration: 0.24 }, 0.04)
        .to(".lp-wash-2", { xPercent: 30, yPercent: 6, duration: 0.24 }, 0.04)
        .to(".lp-wash-1", { yPercent: 8, scale: 1.1, duration: 0.24 }, 0.04)
        .to(".lp-wash-0, .lp-wash-2", { xPercent: 0, yPercent: -14, scale: 0.75, opacity: 0.5, duration: 0.22 }, 0.5)
        .to(".lp-wash-1", { yPercent: -14, scale: 0.75, opacity: 0.5, duration: 0.22 }, 0.5)
        .fromTo(".lp-wash-pool", { opacity: 0, scale: 0.7 }, { opacity: 1, scale: 1, duration: 0.24 }, 0.6)
        .to({}, { duration: 0.16 });
      // the hand-over: the frame settles back and dims as the next section's sheet rises over it
      const mm = gsap.matchMedia();
      mm.add({ fine: "(min-width: 900px) and (pointer: fine)", any: "all" }, (ctx) => {
        const out = gsap.timeline({
          defaults: { ease: "none" },
          scrollTrigger: { trigger: track.current, start: "bottom 200%", end: "bottom bottom", scrub: 0.35 },
        });
        if (ctx.conditions?.fine) out.to(".lp-hero-scene", { scale: 0.92, yPercent: -3, duration: 1 }, 0);
        out.fromTo(".lp-hero-dim", { opacity: 0 }, { opacity: 0.72, duration: 1 }, 0);
      });
      return () => {
        st.kill();
        mm.revert();
      };
    },
    { scope: track, dependencies: [still] },
  );
  const [phase, setPhase] = useState(0);
  useMotionValueEvent(p, "change", (v) => setPhase(v < 0.1 ? 0 : v < 0.64 ? 1 : 2));

  // the odometer rolls up as the combined view arrives, a wheel every 70 ms
  const rolled = phase >= 2;
  useEffect(() => {
    if (still || !rolled) return;
    const ids = Array.from({ length: digitsIn(TOTAL) }, (_, d) => window.setTimeout(() => setLanded((n) => Math.max(n, d + 1)), 140 + d * 55));
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
      // the row of three must fit the frame; the CSS stand-ins take the same width through --cw.
      // Capped by height too, so a short window never squeezes the hand
      const w = Math.min(narrow ? r.width * 0.4 : r.width * 0.165, r.height * 0.27 * ASPECT, 330);
      el.style.setProperty("--cw", `${w.toFixed(1)}px`);
      return { r, narrow, w, h: w / ASPECT };
    };
    let dims = size();
    // render on demand: the loop runs only while something moves, and sleeps when the scroll,
    // the pointer tilt and the deal have all settled
    let drawn = false;
    let last = 0;
    const faces = () => ACCOUNTS.accounts.map((_, i) => ({ art: null, hue: HUES[i] ?? null, h: dims.h }));
    const sizeScene = () => {
      const was = dims.w;
      dims = size();
      scene?.setSize(dims.w, dims.h, (dims.r.width - dims.w) / 2, (dims.r.height - dims.h) / 2);
      // the faces carry the card height: rebuilt with the width, so every card stays 85.6 x 53.98
      if (scene && was !== dims.w) scene.setFaces(faces(), "#000000");
    };
    /** PLAYHEAD: p chases the scroll with a short ease (90 ms) but never faster than the whole
        sequence in 0.7 s, and going forward it rests at each key frame (the labelled row, the
        totals) for a minimum dwell, so a fast flick still shows every stage. Going back it just
        follows. Transform and opacity only downstream; no layout reads here. */
    /** PLAYHEAD: p chases the scroll with one constant smoothing (about Lenis's own lerp), so every
        stretch of scroll moves the cards at the same pace. No holds, no speed cap. */
    const advance = (now: number) => {
      const dt = last ? Math.min(64, now - last) : 16;
      last = now;
      const target = goal.current;
      const v = p.get();
      // phones follow the finger more tightly (native momentum already smooths the scroll)
      const next = Math.abs(target - v) < 0.0004 ? target : v + (target - v) * (1 - Math.exp(-dt / (dims.narrow ? 45 : 110)));
      p.set(next);
      return next;
    };
    // the pointer over the stage (stage px, from its centre) and which card it is over; `lift`
    // eases each card toward the cursor when hovered, so the hand answers the mouse, not the page
    const cur = { x: 0, y: 0, in: false };
    const lift = [0, 0, 0];
    let hover = -1;
    let tick = 0;
    const frame = () => {
      raf = 0;
      if (!alive || !visible || document.hidden) return;
      tilt.x += (aim.x - tilt.x) * 0.08;
      tilt.y += (aim.y - tilt.y) * 0.08;
      const settling = Math.abs(aim.x - tilt.x) + Math.abs(aim.y - tilt.y) > 0.0005;
      const { w, narrow, r } = dims;
      const now = performance.now();
      if (playing.current && !dealt) dealt = now;
      // the deal waits out the preloader's sheet as it lifts off the top of the page
      const since = dealt ? now - dealt - 450 : 0;
      const pv = advance(now);
      // ambient life: once dealt, the hand breathes and turns slowly for as long as the hero is in
      // view and the tab is visible (the IO and visibilitychange stop and wake the loop)
      const sway = narrow ? 0 : clamp01((since - 2200) / 1800) * (1 - clamp01((pv - 0.02) / 0.08) * 0.7);
      const ox = narrow ? 0 : r.width * 0.19;
      const base = ACCOUNTS.accounts.map((_, i) => pose(i, pv, w, narrow, tilt, ox));
      // hit test, front card first: the scaled card's box, a little generous
      let hit = -1;
      if (cur.in && pv < 0.56)
        for (let i = base.length - 1; i >= 0; i--) {
          const q = base[i];
          if (q && Math.abs(cur.x - q.x) < (w * q.s) / 2 + 8 && Math.abs(cur.y - q.y) < (w * q.s) / ASPECT / 2 + 8) {
            hit = i;
            break;
          }
        }
      if (hit !== hover) {
        hover = hit;
        el.dataset.hover = String(hit);
      }
      let lifting = false;
      const poses = base.map((q0, i) => {
        const to = hover === i ? 1 : 0;
        const L = (lift[i] = (lift[i] ?? 0) + (to - (lift[i] ?? 0)) * 0.14);
        if (Math.abs(to - L) > 0.002) lifting = true;
        // magnetic depth: each card sits at its own depth, so the eased tilt pulls the front card most
        const d = 10 + 10 * i;
        // a hovered card rises toward the cursor and turns to face it (after React Bits' Magnet, MIT)
        const q1 = {
          ...q0,
          x: q0.x + tilt.x * d + (cur.x - q0.x) * 0.1 * L,
          y: q0.y + tilt.y * d + (cur.y - q0.y) * 0.1 * L - 18 * L,
          z: q0.z + 90 * L,
          s: q0.s * (1 + 0.05 * L),
          rx: q0.rx * (1 - 0.6 * L),
          ry: q0.ry * (1 - 0.6 * L),
          layer: hover === i ? 9 : q0.layer,
        };
        const q = sway
          ? { ...q1, ry: q1.ry + Math.sin(now / 2600) * 7 * sway, rz: q1.rz + Math.sin(now / 3400 + i * 1.3) * 1.8 * sway, y: q1.y + Math.sin(now / 2100 + i) * 7 * sway }
          : q1;
        // a dealt card: thrown up from below and out of depth, spinning flat as it lands (quartic ease-out)
        const k = 1 - (1 - clamp01((since - 160 - i * 170) / 1400)) ** 4;
        const u = 1 - k;
        return { ...q, rest: q.y, deal: k, y: q.y + u * w * 2.1, z: q.z - u * 520, rz: q.rz * k + u * (14 - i * 14), rx: q.rx + u * 64, ry: q.ry + u * (i - 1) * 18, s: q.s * (0.78 + 0.22 * k) };
      });
      // the peek: the hovered card's balance and categories, beside the cursor
      const pk = peek.current;
      if (pk) pk.style.transform = `translate3d(${Math.min(cur.x + 28, r.width / 2 - 300).toFixed(1)}px, ${(cur.y + 24).toFixed(1)}px, 0)`;
      // each worn row is read into its card as the hand gathers, one after another (a phone lets
      // them go as the hand opens, before the column of cards)
      worn.current.forEach((c, i) => {
        if (!c) return;
        const f = narrow ? span(pv, 0.03, 0.1) : span(pv, 0.42 + i * 0.03, 0.5 + i * 0.03);
        c.style.opacity = String(1 - f);
        c.style.setProperty("--in", f.toFixed(3));
      });
      // a light sweeps across the foil on the deal, then again every 7 s while the hand idles
      const sweep = clamp01(since > 2600 ? ((since - 2600) % 7000) / 1500 : (since - 1100) / 1500);
      const sweeping = sweep > 0 && sweep < 1 && hover < 0;
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
          bk.style.opacity = String(q.deal * (0.7 + 0.3 * (lift[i] ?? 0)));
        }
        // a sheen slides across the face in step with the tilt, opposite the pointer like a real reflection
        const sh = sheens.current[i];
        if (sh) sh.style.transform = `translate3d(${(-tilt.x * 150).toFixed(1)}%, ${(-tilt.y * 50).toFixed(1)}%, 0) skewX(-14deg)`;
        const c = cards.current[i];
        if (c) {
          c.style.transform = `translate(-50%, -50%) translate3d(${q.x}px, ${q.y}px, ${q.z}px) rotateX(${q.rx}deg) rotateY(${q.ry}deg) rotateZ(${q.rz}deg) scale(${q.s})`;
          c.style.zIndex = String(q.layer + 1);
        }
        const l = labels.current[i];
        if (l) {
          l.style.transform = narrow
            ? `translate(0, -50%) translate(${q.x + w / 2 + 14}px, ${q.y}px)`
            : `translate(-50%, 0) translate(${q.x}px, ${q.y + w / ASPECT / 2 + 14}px)`;
          l.style.opacity = String(q.label);
        }
      });
      const moving = Math.abs(goal.current - pv) > 0.0004;
      // idle sway alone renders at half rate
      const idleOnly = sway > 0 && !settling && !lifting && !sweeping && !moving;
      if (scene && (!drawn || tick++ % (idleOnly ? 2 : 1) === 0)) {
        scene.update(
          poses,
          sweeping
            ? { x: mix(-r.width * 0.4, r.width * 0.9, ease(sweep)), y: dims.h * 0.2, on: 1.8 }
            : { x: dims.w / 2 + (cur.in ? cur.x : tilt.x * 260), y: dims.h / 2 + (cur.in ? cur.y : -tilt.y * 200), on: finePointer() ? 1 : 0.4 },
          poses.map(() => null),
        );
        scene.render();
        if (!drawn) {
          drawn = true;
          el.dataset.drawn = ""; // the canvas fades in over the CSS cards only once it has a frame
        }
      }
      // the last card lands at 160 + 2 * 170 + 1400 ms; the idle sway then keeps the loop alive
      const dealing = !dealt || since < 2800;
      if (dealing || sway > 0 || moving || settling || lifting || (scene && !drawn)) raf = requestAnimationFrame(frame);
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
      // a pointer event, not a scroll handler: one rect read here is fine
      const b = el.getBoundingClientRect();
      cur.x = e.clientX - b.left - b.width / 2;
      cur.y = e.clientY - b.top - b.height / 2;
      cur.in = e.clientY >= b.top && e.clientY <= b.bottom;
      // the total's spotlight follows the pointer (after React Bits' SpotlightCard, MIT)
      const cb = combined.current;
      if (cb && el.dataset.phase === "2") {
        const q = cb.getBoundingClientRect();
        cb.style.setProperty("--mx", `${(e.clientX - q.left).toFixed(0)}px`);
        cb.style.setProperty("--my", `${(e.clientY - q.top).toFixed(0)}px`);
      }
      kick();
    };
    const leave = () => {
      cur.in = false;
      kick();
    };
    // the stage's own box, not the window: a box that changed without a window resize left the
    // canvas buffer stretched, which squared the cards off. The buffer now always matches the box.
    const ro =
      typeof ResizeObserver === "function"
        ? new ResizeObserver(() => {
            sizeScene();
            kick();
          })
        : null;
    ro?.observe(el);
    const wake = () => kick();
    kicker.current = kick;
    window.addEventListener("pointermove", move);
    document.documentElement.addEventListener("pointerleave", leave);
    document.addEventListener("visibilitychange", wake);
    if (canvas.current && !liteDevice() && webgl()) {
      import("@/three/cardScene")
        .then(({ createCardScene }) => {
          if (!alive || !canvas.current) return;
          scene = createCardScene(canvas.current, 1600);
          sizeScene();
          scene.setFaces(faces(), "#000000");
          setThree(true);
          kick();
        })
        .catch(() => setThree(false)); // the CSS cards stay
    }
    kick();
    return () => {
      alive = false;
      kicker.current = null;
      cancelAnimationFrame(raf);
      io.disconnect();
      ro?.disconnect();
      window.removeEventListener("pointermove", move);
      document.documentElement.removeEventListener("pointerleave", leave);
      document.removeEventListener("visibilitychange", wake);
      scene?.dispose();
    };
  }, [still, p, playing]);

  // the ambient chips and the loop pause while the hero is off screen
  const [near, setNear] = useState(true);
  useEffect(() => {
    const el = track.current;
    if (!el || still) return;
    const io = new IntersectionObserver(([e]) => setNear(Boolean(e?.isIntersecting)));
    io.observe(el);
    return () => io.disconnect();
  }, [still]);

  const rail = (
    <p className="lp-hero-rail" aria-hidden="true">
      <span className="lp-hero-step">
        <b>0{(still ? 2 : phase) + 1}</b> / 03
      </span>
      <span>{BEATS[still ? 2 : phase]?.stage}</span>
      <i />
    </p>
  );
  const copy = (
    <div className="lp-hero-copy" data-phase={still ? 0 : phase}>
      <h1 id="lp-title" className="lp-display" style={{ "--hd": `${delay}s` } as React.CSSProperties}>
        <Flex lines={HEADLINE_LINES} label={HEADLINE} still={still} play={play} delay={delay} />
      </h1>
      <div className="lp-hero-foot" data-play={play || still ? "" : undefined}>
        <p className="lp-lede">{LINE}</p>
        <div className="lp-actions">
          <Magnetic href="/app" className="lp-pill lp-pill-primary">
            <Roll text="Open the demo" />
          </Magnetic>
          <a className="lp-link" href="#what">
            <Roll text="See what it does" />
          </a>
        </div>
      </div>
    </div>
  );
  const beats = (
    <div className="lp-hero-says">
      {BEATS.slice(1).map((b, k) => (
        <div key={b.stage} className="lp-hero-beat" data-on={still || phase === k + 1 ? "" : undefined}>
          <h2 className="lp-hero-say" style={{ "--hd": "-0.35s" } as React.CSSProperties}>
            <Flex lines={[b.title ?? ""]} label={b.title ?? ""} still={still} play={still || phase === k + 1} delay={-0.4} />
          </h2>
          <p className="lp-hero-say-body">{b.body}</p>
        </div>
      ))}
    </div>
  );

  if (still)
    return (
      <section ref={track} className="lp-hero" data-still="" aria-labelledby="lp-title">
        <div className="lp-hero-pin">
          {rail}
          {copy}
          <StillStage />
          {beats}
        </div>
      </section>
    );

  return (
    <section ref={track} className="lp-hero" data-lite={lite ? "" : undefined} data-off={near ? undefined : ""} aria-labelledby="lp-title">
      {/* one sticky frame holds every plane, so nothing slides against anything while it is pinned */}
      <div className="lp-hero-pin" data-phase={phase}>
        <div className="lp-hero-scene">
        <div className="lp-hero-ground" aria-hidden="true">
          <LedgerField />
        </div>
        {/* the cards' own light on the dark: green, red and teal pools that follow the hand, then
            one pool under the total */}
        <div className="lp-hero-light" aria-hidden="true">
          {HUES.map((h, i) => (
            <span key={h} className={`lp-wash lp-wash-${i}`} style={{ "--h": h } as React.CSSProperties} />
          ))}
          <span className="lp-wash lp-wash-pool" />
        </div>
        {(
          <div ref={stage} className="lp-stage" data-three={three ? "" : undefined} data-phase={phase} data-play={play ? "" : undefined} aria-hidden="true">
            {!lite &&
              ACCOUNTS.accounts.map((a, i) => (
                <span key={a.name} ref={(n) => void (floors.current[i] = n)} className="lp-acard-floor" style={{ "--h": HUES[i] } as React.CSSProperties} />
              ))}
            {!lite &&
              ACCOUNTS.accounts.map((a, i) => (
                <span key={a.name} ref={(n) => void (backs.current[i] = n)} className="lp-acard-back" style={{ "--h": HUES[i] } as React.CSSProperties} />
              ))}
            {/* phones: no canvas at all; the cards are the DOM faces below, moved by transform */}
            {!lite && <canvas ref={canvas} className="lp-stage-canvas" />}
            {ACCOUNTS.accounts.map((a, i) => (
              <div key={a.name} ref={(n) => void (cards.current[i] = n)} className="lp-acard-slot" style={{ "--i": i } as React.CSSProperties}>
                <CardFace i={i} name={a.name} />
                {WORN[i] && (
                  <span ref={(n) => void (worn.current[i] = n)} className="lp-worn" data-at={WORN[i].at}>
                    <span className="lp-chip">
                      <span className="lp-chip-tick" data-ok={WORN[i].ok ? "" : undefined} />
                      <span>{WORN[i].merchant}</span>
                      {WORN[i].amount && <Money value={WORN[i].amount} />}
                      <em>{WORN[i].note}</em>
                    </span>
                  </span>
                )}
                {!lite && (
                  <span className="lp-acard-light">
                    <span ref={(n) => void (sheens.current[i] = n)} className="lp-acard-sheen" />
                  </span>
                )}
              </div>
            ))}
            {ACCOUNTS.accounts.map((a, i) => (
              <div key={a.name} ref={(n) => void (labels.current[i] = n)} className="lp-acct" style={{ "--h": HUES[i] } as React.CSSProperties}>
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
            {/* hover a card: its balance, money in and out, and where the money went */}
            {!lite && (
            <div ref={peek} className="lp-peek">
              {ACCOUNTS.accounts.map((a, i) => (
                <div key={a.name} className="lp-peek-card" data-i={i} style={{ "--h": HUES[i] } as React.CSSProperties}>
                  <p className="lp-peek-name">{a.name}</p>
                  <p className="lp-peek-bal">
                    <Money value={gbp(a.balance)} />
                  </p>
                  <p className="lp-peek-flow">
                    <span>
                      In <Money value={gbp(a.in)} />
                    </span>
                    <span>
                      Out <Money value={gbp(a.out)} />
                    </span>
                  </p>
                  {(SPLITS[a.name] ?? []).length ? (
                    <ul className="lp-peek-cats">
                      {(SPLITS[a.name] ?? []).map((c) => (
                        <li key={c.name} style={{ "--s": c.share } as React.CSSProperties}>
                          <span>{c.name}</span>
                          <span>{Math.round(c.share * 100)}%</span>
                          <i />
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="lp-peek-note">Interest in, nothing spent</p>
                  )}
                </div>
              ))}
            </div>
            )}
            <Combined landed={landed} innerRef={combined} />
          </div>
        )}
        <div className="lp-hero-col">
          {rail}
          <div className="lp-hero-slot">
            {copy}
            {beats}
          </div>
        </div>
        <p className="lp-hero-note" aria-hidden="true">
          A made-up household · every figure summed by the Ledger
        </p>
        </div>
        <span className="lp-hero-dim" aria-hidden="true" />
      </div>
      <p className="lp-sr">
        {ACCOUNTS.accounts.map((a) => `${a.name}: ${formatMoney(gbp(a.balance))}.`).join(" ")} Together: {TOTAL}.
      </p>
    </section>
  );
}
