/* The question's path. On a wide screen with a fine pointer it is a scroll scene: the stage holds
   still while the scroll moves one question through five stops, and Play scrolls for you. A phone,
   a coarse pointer or reduced motion gets the stepped version, which moves only when asked. */
import {
  AnimatePresence,
  type AnimationPlaybackControls,
  animate,
  type MotionValue,
  motion,
  useInView,
  useMotionValueEvent,
  useScroll,
  useTransform,
} from "motion/react";
import { Fragment, type ReactNode, useEffect, useRef, useState } from "react";
import { QuestionChart } from "./Diagrams";
import { FLOW, GROUPS, SAMPLE } from "./nodes";
import { Detail, EASE, NodeButton, StatusTag } from "./parts";

const STEP_MS = 1500;
const N = FLOW.length;
/** seconds Play takes to scroll the whole scene */
const RUN_S = 12;
const FLOW_GROUP = GROUPS[0];

/** The stop for a scene position t (from -0.5 before the first stop to N at the end): -1 before it starts. */
export function stopAt(t: number) {
  return t < 0 ? -1 : Math.min(N - 1, Math.floor(t));
}

function Head({ live }: { live: boolean }) {
  return (
    <div className="ar-flow-head">
      <h2 id="ar-h-flow">{FLOW_GROUP?.title}</h2>
      <div className="ar-flow-side">
        <p className="ar-lede">{FLOW_GROUP?.lede}</p>
        <QuestionChart live={live} />
      </div>
    </div>
  );
}

export default function Flow({ scene, wide, still, playKey }: { scene: boolean; wide: boolean; still: boolean; playKey: number }) {
  return (
    <section className="ar-chapter ar-flow" aria-labelledby="ar-h-flow" id="flow" data-scene={scene || undefined}>
      <Head live={scene} />
      {scene ? <Scene playKey={playKey} /> : <Steps wide={wide} still={still} playKey={playKey} />}
    </section>
  );
}

/* ---------------------------------------------------------------- the scroll scene */

function Scene({ playKey }: { playKey: number }) {
  const track = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({ target: track, offset: ["start start", "end end"] });
  const t = useTransform(scrollYProgress, [0, 1], [-0.5, N]);
  const [stop, setStop] = useState(-1);
  const [open, setOpen] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const run = useRef<AnimationPlaybackControls | null>(null);

  useMotionValueEvent(t, "change", (v) => {
    const s = stopAt(v);
    if (s !== stop) {
      setStop(s);
      setOpen(null);
    }
  });

  const halt = () => {
    run.current?.stop();
    run.current = null;
    setPlaying(false);
  };

  const play = (fromTop: boolean) => {
    const el = track.current;
    if (!el) return;
    halt();
    const top = el.getBoundingClientRect().top + window.scrollY;
    const end = top + el.offsetHeight - window.innerHeight;
    const y = window.scrollY;
    const inside = y >= top && y < end - 4 && !fromTop;
    const start = inside ? y : top;
    const span = end - top || 1;
    const seconds = (RUN_S * (end - start)) / span;
    // a jump to the scene first, when it is elsewhere, then one steady pass through it
    const lead = inside ? 0 : Math.min(0.9, Math.abs(y - top) / 1500 + 0.3);
    run.current = animate(y, inside ? [y, end] : [y, top, end], {
      duration: lead + seconds,
      times: inside ? [0, 1] : [0, lead / (lead + seconds), 1],
      ease: inside ? "linear" : ["easeInOut", "linear"],
      onUpdate: (v: number) => window.scrollTo(0, v),
      onComplete: () => {
        run.current = null;
        setPlaying(false);
      },
    });
    setPlaying(true);
  };

  useEffect(() => {
    if (playKey > 0) play(true);
  }, [playKey]);

  // the reader's own scroll or key takes over from Play
  useEffect(() => {
    if (!playing) return;
    const stopIt = () => halt();
    window.addEventListener("wheel", stopIt, { passive: true });
    window.addEventListener("touchstart", stopIt, { passive: true });
    window.addEventListener("keydown", stopIt);
    return () => {
      window.removeEventListener("wheel", stopIt);
      window.removeEventListener("touchstart", stopIt);
      window.removeEventListener("keydown", stopIt);
    };
  }, [playing]);

  useEffect(() => () => run.current?.stop(), []);

  const noteNode = FLOW.find((n) => n.id === open) ?? FLOW[Math.max(0, stop)];
  const done = stop === N - 1;

  return (
    <div className="ar-track" ref={track}>
      <div className="ar-scene">
        <div className="ar-ask">
          <motion.p className="ar-bubble" style={{ opacity: useTransform(t, [-0.5, -0.2], [0.55, 1]) }}>
            {SAMPLE.question}
          </motion.p>
          <button type="button" className="ar-pill" onClick={playing ? halt : () => play(done)}>
            {playing ? "Pause" : done ? "Play again" : "Play"}
          </button>
        </div>
        <p className="ar-caption" aria-live="polite">
          {stop >= 0 ? SAMPLE.steps[stop] : "Scroll, or press Play, to follow this question through the system."}
        </p>
        <ol className="ar-path">
          {FLOW.map((n, i) => (
            <Fragment key={n.id}>
              <li className="ar-path-node">
                <NodeButton
                  node={n}
                  open={noteNode?.id === n.id && stop >= 0}
                  lit={stop >= i}
                  current={stop === i}
                  onToggle={() => setOpen(n.id)}
                />
              </li>
              {i < N - 1 && <Wire t={t} i={i} />}
            </Fragment>
          ))}
        </ol>
        <Rail t={t} />
        <div className="ar-scene-low">
          <div className="ar-frames" aria-hidden="true">
            {FRAMES.map((F, i) => (
              <Frame key={FLOW[i]?.id} t={t} i={i}>
                <F t={t} />
              </Frame>
            ))}
          </div>
          <AnimatePresence mode="wait" initial={false}>
            {noteNode && (
              <motion.div
                key={noteNode.id}
                id={`ar-d-${noteNode.id}`}
                className="ar-note"
                role="region"
                aria-label={noteNode.title}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.2, ease: EASE }}
              >
                <div className="ar-detail-head">
                  <h3>{noteNode.title}</h3>
                  <StatusTag status={noteNode.status} />
                </div>
                <p>{noteNode.decision}</p>
                <p className="ar-note-why">{noteNode.why}</p>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}

/** The question itself, riding a rail under the stops from the first to the last. */
function Rail({ t }: { t: MotionValue<number> }) {
  const p = useTransform(t, [0, N - 1], [0, 1], { clamp: true });
  const x = useTransform(p, (v) => `${v * 100}%`);
  const opacity = useTransform(t, [-0.3, 0], [0, 1]);
  return (
    <div className="ar-rail" aria-hidden="true">
      <motion.span className="ar-rail-fill" style={{ scaleX: p }} />
      <motion.span className="ar-rail-carrier" style={{ x, opacity }}>
        <span className="ar-packet" />
      </motion.span>
    </div>
  );
}

function Wire({ t, i }: { t: MotionValue<number>; i: number }) {
  const scaleX = useTransform(t, [i + 0.55, i + 1], [0, 1]);
  return (
    <li className="ar-wire" aria-hidden="true">
      <motion.span className="ar-wire-fill" style={{ scaleX }} />
    </li>
  );
}

/** One stop's picture: in while the scene is on its stop, out as the next arrives (the last one stays). */
function Frame({ t, i, children }: { t: MotionValue<number>; i: number; children: ReactNode }) {
  const last = i === N - 1;
  const opacity = useTransform(t, last ? [i - 0.1, i] : [i - 0.1, i, i + 0.9, i + 1], last ? [0, 1] : [0, 1, 1, 0]);
  const y = useTransform(t, [i - 0.1, i], [16, 0]);
  return (
    <motion.div className="ar-frame" style={{ opacity, y }}>
      {children}
    </motion.div>
  );
}

/** A part of a frame that arrives at scene position `at`. */
function Beat({ t, at, children, className }: { t: MotionValue<number>; at: number; children: ReactNode; className?: string }) {
  const opacity = useTransform(t, [at, at + 0.12], [0, 1]);
  const y = useTransform(t, [at, at + 0.12], [12, 0]);
  return (
    <motion.div className={className} style={{ opacity, y }}>
      {children}
    </motion.div>
  );
}

function Tick({ t, at }: { t: MotionValue<number>; at: number }) {
  const pathLength = useTransform(t, [at, at + 0.14], [0, 1]);
  return (
    <svg className="ar-tick" viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="10" cy="10" r="10" />
      <motion.path d="M5.5 10.5l3 3 6-7" style={{ pathLength }} />
    </svg>
  );
}

const REPLY = ["Eating out is", "£64", "above your usual month,", "the easiest place to save.", "2", "subscriptions renewed on", "3 November."];
const FIGURES: [string, string][] = [
  ["£64", "spending_summary"],
  ["2", "list_insights"],
  ["3 November", "list_insights"],
];

type F = (p: { t: MotionValue<number> }) => ReactNode;

const FRAMES: F[] = [
  ({ t }) => (
    <div className="ar-card">
      <p className="ar-card-name">Read as</p>
      {(
        [
          ["this month", "1 to 30 November"],
          ["save", "Guidance and Insights"],
          ["model calls", "0"],
        ] as const
      ).map(([a, b], k) => (
        <Beat key={a} t={t} at={0.1 + k * 0.2} className="ar-row">
          <span>{a}</span>
          <strong>{b}</strong>
        </Beat>
      ))}
    </div>
  ),
  ({ t }) => (
    <div className="ar-cards">
      {(
        [
          ["spending_summary", "November, by Category"],
          ["list_guidance", "3 cards"],
          ["list_insights", "3 Insights"],
        ] as const
      ).map(([a, b], k) => (
        <Beat key={a} t={t} at={1.1 + k * 0.2} className="ar-card ar-call">
          <code>{a}</code>
          <span>{b}</span>
        </Beat>
      ))}
    </div>
  ),
  ({ t }) => (
    <div className="ar-card ar-words">
      <p className="ar-card-name">One writer call, no tools</p>
      <div className="ar-draft">
        {REPLY.map((w, k) => (
          <Beat key={w} t={t} at={2.1 + k * 0.1} className={FIGURES.some(([f]) => w.startsWith(f)) ? "ar-w ar-fig" : "ar-w"}>
            {w}
          </Beat>
        ))}
      </div>
    </div>
  ),
  ({ t }) => (
    <div className="ar-card">
      <p className="ar-card-name">Each figure against this turn's tool results</p>
      {FIGURES.map(([f, tool], k) => (
        <Beat key={f} t={t} at={3.1 + k * 0.22} className="ar-row">
          <span className="ar-fig">{f}</span>
          <span className="ar-match">
            <code>{tool}</code>
            <Tick t={t} at={3.18 + k * 0.22} />
          </span>
        </Beat>
      ))}
    </div>
  ),
  ({ t }) => (
    <div className="ar-card ar-out">
      <p className="ar-reply">{REPLY.join(" ")}</p>
      <Beat t={t} at={4.2} className="ar-evidence">
        <span>Evidence: 3 rows</span>
        <span>Read as 1 to 30 November</span>
        <span>Answered by the planner</span>
      </Beat>
    </div>
  ),
];

/* ---------------------------------------------------------------- the stepped version */

function Steps({ wide, still, playKey }: { wide: boolean; still: boolean; playKey: number }) {
  const stage = useRef<HTMLDivElement>(null);
  const seen = useInView(stage, { once: true, amount: 0.5 });
  // reduced motion rests on the whole path, lit; otherwise it waits for the scroll or Play
  const [step, setStep] = useState(still ? N - 1 : -1);
  const [playing, setPlaying] = useState(false);
  const [open, setOpen] = useState<string | null>(wide ? (FLOW[0]?.id ?? null) : null);

  const play = () => {
    setStep(0);
    setPlaying(true);
    if (wide) setOpen(FLOW[0]?.id ?? null);
  };

  useEffect(() => {
    if (seen && !still) play();
  }, [seen]);

  useEffect(() => {
    if (playKey > 0) play();
  }, [playKey]);

  useEffect(() => {
    if (!playing) return;
    if (step >= N - 1) {
      setPlaying(false);
      return;
    }
    const t = setTimeout(() => {
      setStep((s) => s + 1);
      if (wide) setOpen(FLOW[step + 1]?.id ?? null);
    }, STEP_MS);
    return () => clearTimeout(t);
  }, [playing, step, wide]);

  const shown = FLOW.find((n) => n.id === open);
  const fill = { duration: still ? 0 : 0.6, ease: EASE };
  const toggle = (id: string) => {
    setPlaying(false);
    setOpen((o) => (o === id && !wide ? null : id));
  };

  return (
    <div className="ar-stage" ref={stage}>
      <div className="ar-ask">
        <p className="ar-bubble">{SAMPLE.question}</p>
        <button type="button" className="ar-pill" onClick={playing ? () => setPlaying(false) : play}>
          {playing ? "Pause" : step >= N - 1 ? "Play again" : "Play"}
        </button>
      </div>
      <p className="ar-caption" aria-live="polite">
        {step >= 0 ? SAMPLE.steps[step] : "Press Play to follow this question through the system."}
      </p>
      <ol className="ar-path">
        {FLOW.map((n, i) => (
          <Fragment key={n.id}>
            <li className="ar-path-node">
              <NodeButton
                node={n}
                open={open === n.id}
                lit={step >= i}
                current={step === i && playing}
                onToggle={() => toggle(n.id)}
              />
              {!wide && open === n.id && <Detail node={n} id={`ar-d-${n.id}`} />}
            </li>
            {i < N - 1 && (
              <li className="ar-wire" aria-hidden="true">
                <motion.span
                  className="ar-wire-fill"
                  initial={false}
                  animate={wide ? { scaleX: step > i ? 1 : 0, scaleY: 1 } : { scaleY: step > i ? 1 : 0, scaleX: 1 }}
                  transition={fill}
                />
              </li>
            )}
          </Fragment>
        ))}
      </ol>
      {wide && shown && <Detail node={shown} id={`ar-d-${shown.id}`} />}
    </div>
  );
}
