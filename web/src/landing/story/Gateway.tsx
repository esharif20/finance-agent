import { type MotionValue, motion, useMotionTemplate, useMotionValue, useScroll, useSpring, useTransform } from "motion/react";
import { type PointerEvent, useRef, useState } from "react";
import { Money } from "@/components/Money";
import { formatMoney } from "@/lib/money";
import { finePointer } from "../fx";
import { GATEWAY } from "./story.fixture";

const LEAD =
  "When you turn the cloud model on, names and amounts are swapped for placeholders before anything leaves. The real values are filled back in on your Mac.";

/** The water's surface: one wave, drawn once, that drifts sideways as you scroll. */
function Wave({ x }: { x?: MotionValue<string> }) {
  return (
    <motion.div className="st-wave" data-wave="" style={x ? { x } : undefined} aria-hidden="true">
      <svg viewBox="0 0 2880 40" preserveAspectRatio="none">
        <path d="M0 20 C 240 0, 480 0, 720 20 S 1200 40, 1440 20 S 1920 0, 2160 20 S 2640 40, 2880 20 L 2880 40 L 0 40 Z" />
      </svg>
    </motion.div>
  );
}

function Real() {
  return (
    <p className="st-line">
      <span>
        <Money value={GATEWAY.amount} /> at
      </span>
      <span>{GATEWAY.merchant}</span>
    </p>
  );
}

function Token() {
  return (
    <p className="st-line">
      <span>
        <span className="st-token">{GATEWAY.amountToken}</span> at
      </span>
      <span className="st-token">{GATEWAY.merchantToken}</span>
    </p>
  );
}

/** The signature move: black water rises through the line on your statement. What it covers turns
    into the placeholders the cloud model sees; a lens under the pointer shows what only your Mac can
    read. Then the water drains and the real values come back. */
function Moving() {
  const track = useRef<HTMLDivElement>(null);
  const [lensLive] = useState(finePointer);
  const [pinned, setPinned] = useState(false);
  const { scrollYProgress } = useScroll({ target: track, offset: ["start start", "end end"] });
  const p = useSpring(scrollYProgress, { stiffness: 110, damping: 26, restDelta: 0.0005 });
  // seam in % of the stage: dry, rises over the line, holds, drains back
  const seam = useTransform(p, [0.06, 0.4, 0.62, 0.94], [100, 27, 27, 100]);
  const waterY = useTransform(seam, (s) => `${s}%`);
  const above = useTransform(seam, (s) => `inset(0 0 ${100 - s}% 0)`);
  const below = useMotionTemplate`inset(${seam}% 0 0 0)`;
  const drift = useTransform(p, [0, 1], ["0%", "-50%"]);
  const mac = useTransform(seam, [60, 40], [1, 0]);
  const cloud = useTransform(seam, [60, 40], [0, 1]);
  // the foot is set in lemon for the water, so it only shows once the water has reached it
  const foot = useTransform(seam, [92, 78], [0, 1]);

  const lx = useMotionValue(0);
  const ly = useMotionValue(0);
  const radius = useMotionValue(0);
  const r = useSpring(radius, { stiffness: 260, damping: 26 });
  // the lens trails the pointer a little (MotionSites "spotlight reveal mask": the lag makes it physical)
  const tx = useSpring(lx, { stiffness: 300, damping: 30 });
  const ty = useSpring(ly, { stiffness: 300, damping: 30 });
  const lens = useMotionTemplate`circle(${r}px at ${tx}px ${ty}px)`;
  const move = (e: PointerEvent<HTMLDivElement>) => {
    if (!lensLive || pinned || e.pointerType !== "mouse") return;
    const b = e.currentTarget.getBoundingClientRect();
    // snap to the first position instead of flying in from the corner
    if (radius.get() === 0) {
      tx.jump(e.clientX - b.left);
      ty.jump(e.clientY - b.top);
    }
    lx.set(e.clientX - b.left);
    ly.set(e.clientY - b.top);
    // the lens opens only under the surface, where there is something hidden to see
    radius.set((e.clientY - b.top) / b.height > seam.get() / 100 ? 120 : 0);
  };
  const leave = () => !pinned && radius.set(0);
  const toggle = (e: React.MouseEvent<HTMLButtonElement>) => {
    const stage = e.currentTarget.closest(".st-gate-stage")?.getBoundingClientRect();
    if (stage) {
      lx.set(stage.width / 2);
      ly.set(stage.height / 2);
    }
    radius.set(pinned ? 0 : 2400);
    setPinned(!pinned);
  };

  return (
    <div ref={track} className="st-gate-track">
      <div className="st-gate-stage" data-pinned={pinned ? "" : undefined} onPointerMove={move} onPointerLeave={leave}>
        <h2 id="st-gate-title" className="lp-h2 st-gate-title">
          What leaves your Mac.
        </h2>
        <p className="lp-sr">
          {formatMoney(GATEWAY.amount)} at {GATEWAY.merchant} is sent as {GATEWAY.amountToken} at {GATEWAY.merchantToken}.
        </p>
        <motion.div className="st-water" style={{ y: waterY }} aria-hidden="true">
          <Wave x={drift} />
        </motion.div>
        <motion.div className="st-plane st-plane-real" style={{ clipPath: above }} aria-hidden="true">
          <Real />
        </motion.div>
        <motion.div className="st-plane st-plane-token" style={{ clipPath: below }} aria-hidden="true">
          <Token />
        </motion.div>
        <motion.div className="st-plane st-plane-lens" style={{ clipPath: lens }} aria-hidden="true">
          <Real />
        </motion.div>
        <motion.div className="st-gate-foot lp-wrap" style={pinned ? undefined : { opacity: foot }}>
          <p className="st-gate-label" aria-hidden="true">
            <motion.span style={{ opacity: mac }}>On your Mac</motion.span>
            <motion.span style={{ opacity: cloud }}>What the cloud model sees</motion.span>
          </p>
          <p className="st-gate-lead">{LEAD}</p>
          <button type="button" className="st-gate-peek" aria-pressed={pinned} onClick={toggle}>
            {pinned ? "Hide the real values" : "See what stays on your Mac"}
          </button>
        </motion.div>
      </div>
    </div>
  );
}

function Still() {
  return (
    <div className="st-gate-still">
      <div className="st-gate-dry lp-wrap">
        <h2 id="st-gate-title" className="lp-h2 st-gate-title">
          What leaves your Mac.
        </h2>
        <p className="st-gate-label">On your Mac</p>
        <Real />
      </div>
      <div className="st-gate-wet">
        <Wave />
        <div className="lp-wrap">
          <p className="st-gate-label">What the cloud model sees</p>
          <Token />
          <p className="st-gate-lead">{LEAD}</p>
        </div>
      </div>
    </div>
  );
}

export function Gateway({ still }: { still: boolean }) {
  return (
    <section className="st-gate" aria-labelledby="st-gate-title">
      {still ? <Still /> : <Moving />}
    </section>
  );
}
