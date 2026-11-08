import { motion, type MotionStyle, type MotionValue, useScroll, useSpring, useTransform } from "motion/react";
import { type ReactNode, useRef, useState } from "react";
import { formatMoney } from "@/lib/money";
import { Field as Ambient } from "../fx";
import { GATEWAY } from "./story.fixture";

const REAL = { amount: formatMoney(GATEWAY.amount), merchant: GATEWAY.merchant };
const SENT = { amount: GATEWAY.amountLabel, merchant: GATEWAY.merchantLabel };

const STEPS = [
  "Your Mac swaps the amount and the shop’s name for plain labels. The category stays, so the question still makes sense.",
  "Only the labelled version is sent, and only when you have turned the cloud model on for that conversation.",
  "The answer comes back with the labels in it, and your Mac puts the real values back before you read it.",
];

const SR = `${REAL.amount} at ${REAL.merchant}, ${GATEWAY.category}, is sent to the cloud model as ${SENT.amount} at ${SENT.merchant}, ${GATEWAY.category}. Its answer comes back with the labels, and your Mac fills the real values back in.`;

/** One field that turns from the real value into its label as `--m` goes from 0 to 1. Both sit in
    one grid cell, so the row keeps its width while it changes. */
function Field({ real, sent }: { real: string; sent: string }) {
  return (
    <span className="st-gw-field">
      <span className="st-gw-real">{real}</span>
      <span className="st-gw-sent">{sent}</span>
    </span>
  );
}

function Payment() {
  return (
    <span className="st-gw-row">
      <Field real={REAL.merchant} sent={SENT.merchant} />
      <span className="st-gw-cat">{GATEWAY.category}</span>
      <Field real={REAL.amount} sent={SENT.amount} />
    </span>
  );
}

function Reply() {
  return (
    <span className="st-gw-row st-gw-reply">
      <span>You spent</span>
      <Field real={REAL.amount} sent={SENT.amount} />
      <span>at</span>
      <Field real={REAL.merchant} sent={SENT.merchant} />
    </span>
  );
}

/** A labelled arrow, drawn as `--a` goes from 0 to 1. */
function Arrow({ label, className, back = false }: { label: string; className: string; back?: boolean }) {
  return (
    <span className={`st-gw-arrow ${className}`}>
      <svg viewBox="0 0 100 12" preserveAspectRatio="none" aria-hidden="true">
        <path pathLength={100} d={back ? "M98 6 H4 M11 1 L3 6 L11 11" : "M2 6 H96 M89 1 L97 6 L89 11"} />
      </svg>
      <span className="st-gw-arrow-label">{label}</span>
    </span>
  );
}

/** The two sides and the gateway between them. `out` and `back` are the travelling copies; they
    sit in the Mac's slots and are moved across with container units. */
function Lane({ out, back }: { out?: ReactNode; back?: ReactNode }) {
  return (
    <div className="st-gw-lane">
      <div className="st-gw-panel st-gw-mac">
        <p className="st-gw-label">On your Mac</p>
        <span className="st-gw-slot st-gw-home">
          <Payment />
          {out}
        </span>
        <span className="st-gw-keep">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 21 V5 M6 11 L12 4 L18 11" />
          </svg>
          Stays on your Mac: the real amount and the shop’s name
        </span>
        <span className="st-gw-slot st-gw-slot-reply st-gw-landing">
          <span className="st-gw-rest">
            <Reply />
          </span>
          {back}
        </span>
      </div>
      <div className="st-gw-gate">
        <span>Privacy gateway, on your Mac</span>
      </div>
      <div className="st-gw-panel st-gw-cloud">
        <p className="st-gw-label">What the cloud model sees</p>
        <span className="st-gw-slot st-gw-arrival">
          <Payment />
        </span>
        <span className="st-gw-keep st-gw-spacer" />
        <span className="st-gw-slot st-gw-slot-reply st-gw-arrival">
          <Reply />
        </span>
      </div>
      <Arrow label="Sent only if you opt in" className="st-gw-arrow-out" />
      <Arrow label="The answer comes back" className="st-gw-arrow-back" back />
    </div>
  );
}

const vars = (v: Record<string, MotionValue<number>>) => v as unknown as MotionStyle;

/** A copy that travels across the lane (`--t`: 0 at the Mac, 1 at the cloud) and turns into its
    labels as it passes the gateway (`--m`). */
function Packet({ t, m, o, reply = false }: { t: MotionValue<number>; m: MotionValue<number>; o?: MotionValue<number>; reply?: boolean }) {
  return (
    <motion.span className="st-gw-packet" style={vars(o ? { "--t": t, "--m": m, "--o": o } : { "--t": t, "--m": m })}>
      {reply ? <Reply /> : <Payment />}
    </motion.span>
  );
}

function Step({ step, i, text }: { step: MotionValue<number>; i: number; text: string }) {
  const opacity = useTransform(step, (s) => (Math.round(s) === i ? 1 : 0.34));
  return <motion.li style={{ opacity }}>{text}</motion.li>;
}

/** The signature move, explained: one made-up payment leaves the Mac, loses its amount and shop
    name at the gateway, and its answer comes back and gets them back. Scroll drives it. */
function Moving() {
  const track = useRef<HTMLDivElement>(null);
  // a phone shows the lane as a column in the page flow, so it plays while it scrolls past, not pinned
  const [narrow] = useState(() => typeof window.matchMedia === "function" && window.matchMedia("(max-width: 719.98px)").matches);
  const { scrollYProgress } = useScroll({ target: track, offset: narrow ? ["start 0.75", "end 0.95"] : ["start start", "end end"] });
  const p = useSpring(scrollYProgress, { stiffness: 110, damping: 26, restDelta: 0.0005 });
  const c = { clamp: true };
  const outT = useTransform(p, [0.12, 0.42], [0, 1], c);
  const outM = useTransform(p, [0.22, 0.32], [0, 1], c);
  const backT = useTransform(p, [0.58, 0.86], [1, 0], c);
  const backM = useTransform(p, [0.68, 0.78], [1, 0], c);
  const backO = useTransform(p, [0.5, 0.56], [0, 1], c);
  const lane = vars({
    "--a-out": useTransform(p, [0.06, 0.26], [0, 1], c),
    "--a-back": useTransform(p, [0.54, 0.68], [0, 1], c),
    "--keep": useTransform(p, [0.04, 0.14], [0, 1], c),
    "--land": useTransform(p, [0.86, 0.9], [0, 1], c),
  });
  const step = useTransform(p, [0.2, 0.42, 0.62], [0, 1, 2], c);
  return (
    <div ref={track} className="st-gate-track">
      <div className="st-gate-stage">
        <div className="lp-wrap st-gw">
          <h2 id="st-gate-title" className="lp-h2 st-gate-title">
            What leaves your Mac.
          </h2>
          <p className="st-gw-lead">Nothing, unless you turn the cloud model on. Then this is one payment on its way out.</p>
          <p className="lp-sr">{SR}</p>
          <motion.div className="st-gw-wrap" style={lane} aria-hidden="true">
            <Lane out={<Packet t={outT} m={outM} />} back={<Packet t={backT} m={backM} o={backO} reply />} />
          </motion.div>
          <ol className="st-gw-steps">
            {STEPS.map((s, i) => (
              <Step key={s} step={step} i={i} text={s} />
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}

function Still() {
  return (
    <div className="lp-wrap st-gw">
      <h2 id="st-gate-title" className="lp-h2 st-gate-title">
        What leaves your Mac.
      </h2>
      <p className="st-gw-lead">Nothing, unless you turn the cloud model on. Then this is one payment on its way out.</p>
      <p className="lp-sr">{SR}</p>
      <div className="st-gw-wrap" aria-hidden="true">
        <Lane />
      </div>
      <ol className="st-gw-steps">
        {STEPS.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ol>
    </div>
  );
}

export function Gateway({ still }: { still: boolean }) {
  return (
    <section className="st-gate" aria-labelledby="st-gate-title" data-still={still ? "" : undefined}>
      <Ambient tone="lemon" />
      {still ? <Still /> : <Moving />}
    </section>
  );
}
