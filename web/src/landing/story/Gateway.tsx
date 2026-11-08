import { type MotionValue, motion, useScroll, useTransform } from "motion/react";
import { type ReactNode, useRef } from "react";
import { Money } from "@/components/Money";
import { formatMoney } from "@/lib/money";
import { GATEWAY } from "./story.fixture";

const LEAD = "When you turn the cloud model on, names and amounts are swapped for placeholders before anything leaves. The real values are filled back in on your Mac.";

/** One word that turns over to its placeholder: the real one lifts out as the token settles in. Whole words, never scrambled digits. */
function Flip({ t, real, token }: { t: MotionValue<number>; real: ReactNode; token: string }) {
  const realOpacity = useTransform(t, [0, 0.5], [1, 0]);
  const realY = useTransform(t, [0, 0.5], ["0em", "-0.35em"]);
  const tokenOpacity = useTransform(t, [0.5, 1], [0, 1]);
  const tokenY = useTransform(t, [0.5, 1], ["0.35em", "0em"]);
  return (
    <span className="st-flip">
      <motion.span className="st-flip-real" style={{ opacity: realOpacity, y: realY }}>
        {real}
      </motion.span>
      <motion.span className="st-flip-token" style={{ opacity: tokenOpacity, y: tokenY }}>
        {token}
      </motion.span>
    </span>
  );
}

/** The #76 coin: the pound face turns over to the placeholder face as the line is redacted. Still: the pound face only. */
function Coin({ rotateY }: { rotateY?: MotionValue<number> }) {
  return (
    <div className="st-coin-stage" aria-hidden="true">
      {rotateY ? <TurningCoin rotateY={rotateY} /> : <div className="st-coin"><span className="st-coin-face">£</span></div>}
    </div>
  );
}

/** WebKit ignores backface-visibility here, so each face switches off as it passes edge-on. */
function TurningCoin({ rotateY }: { rotateY: MotionValue<number> }) {
  const front = useTransform(rotateY, [89, 91], [1, 0]);
  const back = useTransform(rotateY, [89, 91], [0, 1]);
  return (
    <motion.div className="st-coin" style={{ rotateY }}>
      <motion.span className="st-coin-face" style={{ opacity: front }}>
        £
      </motion.span>
      <motion.span className="st-coin-face st-coin-back" style={{ opacity: back }}>
        {"{P}"}
      </motion.span>
    </motion.div>
  );
}

function Wave({ still }: { still: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start end", "end end"] });
  const scaleY = useTransform(scrollYProgress, [0, 1], [0, 1]);
  return (
    <motion.div ref={ref} className="st-wave" data-wave="" style={still ? undefined : { scaleY }} aria-hidden="true">
      <svg viewBox="0 0 1440 120" preserveAspectRatio="none">
        <path d="M0 64 C 240 8, 480 8, 720 56 S 1200 120, 1440 48 L 1440 120 L 0 120 Z" />
      </svg>
    </motion.div>
  );
}

function Moving() {
  const ref = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start start", "end end"] });
  // real, then placeholders while held, then back to real
  const t = useTransform(scrollYProgress, [0.12, 0.38, 0.62, 0.88], [0, 1, 1, 0]);
  const rotateY = useTransform(t, [0, 1], [0, 180]);
  const macOpacity = useTransform(t, [0, 0.5], [1, 0]);
  const cloudOpacity = useTransform(t, [0.5, 1], [0, 1]);
  return (
    <div ref={ref} className="st-gate-track">
      <div className="st-gate-stage lp-wrap">
        <Coin rotateY={rotateY} />
        <div className="st-gate-copy">
          <p className="st-gate-label" aria-hidden="true">
            <motion.span style={{ opacity: macOpacity }}>On your Mac</motion.span>
            <motion.span style={{ opacity: cloudOpacity }}>What the cloud model sees</motion.span>
          </p>
          <p className="st-gate-line">
            <span className="lp-sr">
              {formatMoney(GATEWAY.amount)} at {GATEWAY.merchant} is sent as {GATEWAY.amountToken} at {GATEWAY.merchantToken}.
            </span>
            <span aria-hidden="true">
              <Flip t={t} real={<Money value={GATEWAY.amount} />} token={GATEWAY.amountToken} /> at{" "}
              <Flip t={t} real={GATEWAY.merchant} token={GATEWAY.merchantToken} />
            </span>
          </p>
          <p className="st-gate-lead">{LEAD}</p>
        </div>
      </div>
    </div>
  );
}

function Still() {
  return (
    <div className="st-gate-stage st-gate-still lp-wrap">
      <Coin />
      <div className="st-gate-copy">
        <p className="st-gate-label">On your Mac</p>
        <p className="st-gate-line">
          <Money value={GATEWAY.amount} /> at {GATEWAY.merchant}
        </p>
        <p className="st-gate-label">What the cloud model sees</p>
        <p className="st-gate-line">
          <span className="st-token">{GATEWAY.amountToken}</span> at <span className="st-token">{GATEWAY.merchantToken}</span>
        </p>
        <p className="st-gate-lead">{LEAD}</p>
      </div>
    </div>
  );
}

export function Gateway({ still }: { still: boolean }) {
  return (
    <section className="st-gate" aria-labelledby="st-gate-title">
      <h2 id="st-gate-title" className="lp-h2 st-gate-title lp-wrap">
        What leaves your Mac.
      </h2>
      {still ? <Still /> : <Moving />}
      <Wave still={still} />
    </section>
  );
}
