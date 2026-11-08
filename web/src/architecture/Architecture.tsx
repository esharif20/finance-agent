import { MotionConfig, motion, useInView } from "motion/react";
import { Fragment, useEffect, useRef, useState } from "react";
import { type ArchNode, FLOW, GROUPS, type Group, NODES, REPO, SAMPLE, STATUS_LABEL, type Status } from "./nodes";
import "./architecture.css";

const STEP_MS = 1500;
const [FLOW_GROUP, ...CHAPTERS] = GROUPS as [Group, ...Group[]];
const EASE = [0.22, 1, 0.36, 1] as const;

function query(q: string) {
  return typeof window.matchMedia === "function" && window.matchMedia(q).matches;
}

/** Read once per mount, like the landing page, so a test can switch it between renders. */
const useStill = () => useState(() => query("(prefers-reduced-motion: reduce)"))[0];

function useWide() {
  const q = "(min-width: 900px)";
  const [wide, setWide] = useState(() => query(q));
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const m = window.matchMedia(q);
    const on = () => setWide(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return wide;
}

function Mark({ status }: { status: Status }) {
  return <span className={`ar-mark ar-mark-${status}`} aria-hidden="true" />;
}

function StatusTag({ status }: { status: Status }) {
  return (
    <span className="ar-status">
      <Mark status={status} />
      {STATUS_LABEL[status]}
    </span>
  );
}

function Detail({ node, id }: { node: ArchNode; id: string }) {
  return (
    <motion.div
      id={id}
      key={node.id}
      className="ar-detail"
      role="region"
      aria-label={node.title}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.24, ease: EASE }}
    >
      <div className="ar-detail-head">
        <h3>{node.title}</h3>
        <StatusTag status={node.status} />
      </div>
      <div className="ar-pair">
        <h4>Decision</h4>
        <p>{node.decision}</p>
      </div>
      <div className="ar-pair">
        <h4>Why</h4>
        <p>{node.why}</p>
      </div>
      {node.note && (
        <div className="ar-pair">
          <h4>Where it stands</h4>
          <p>{node.note}</p>
        </div>
      )}
      <div className="ar-pair">
        <h4>Turned down</h4>
        <ul className="ar-rejected">
          {node.rejected.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      </div>
      <div className="ar-pair">
        <h4>Sources</h4>
        <ul className="ar-sources">
          {node.sources.map((s) => (
            <li key={s}>
              <a href={`${REPO}/blob/main/${s}`}>
                <code>{s}</code>
              </a>
            </li>
          ))}
          {node.issues?.map((n) => (
            <li key={n}>
              <a href={`${REPO}/issues/${n}`}>Issue #{n}</a>
            </li>
          ))}
        </ul>
      </div>
    </motion.div>
  );
}

function NodeButton({
  node,
  open,
  lit,
  current,
  onToggle,
}: {
  node: ArchNode;
  open: boolean;
  lit?: boolean;
  current?: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      className="ar-node"
      data-open={open || undefined}
      data-lit={lit || undefined}
      data-current={current || undefined}
      aria-expanded={open}
      aria-controls={`ar-d-${node.id}`}
      onClick={onToggle}
    >
      <span className="ar-node-title">{node.title}</span>
      <span className="ar-node-short">{node.short}</span>
      <StatusTag status={node.status} />
    </button>
  );
}

/** One chapter: its nodes, and the open node's detail beside them (wide) or under the node (phone). */
function Chapter({ group, wide }: { group: Group; wide: boolean }) {
  const nodes = NODES.filter((n) => n.group === group.id);
  const [open, setOpen] = useState<string | null>(wide ? (nodes[0]?.id ?? null) : null);
  const shown = nodes.find((n) => n.id === open);
  const toggle = (id: string) => setOpen((o) => (o === id && !wide ? null : id));
  return (
    <section className={`ar-chapter ar-${group.id}`} id={`ar-${group.id}`} aria-labelledby={`ar-h-${group.id}`}>
      <div className="ar-chapter-text">
        <h2 id={`ar-h-${group.id}`}>{group.title}</h2>
        <p className="ar-lede">{group.lede}</p>
        {group.id === "context" && <PromptStrip />}
      </div>
      <div className="ar-chapter-body">
        <ul className="ar-nodes" data-layout={group.id}>
          {nodes.map((n) => (
            <li key={n.id}>
              <NodeButton node={n} open={open === n.id} onToggle={() => toggle(n.id)} />
              {!wide && open === n.id && <Detail node={n} id={`ar-d-${n.id}`} />}
            </li>
          ))}
        </ul>
        {wide && shown && <Detail node={shown} id={`ar-d-${shown.id}`} />}
      </div>
    </section>
  );
}

/** The prompt's fixed order, with the part the KV cache reuses marked. */
function PromptStrip() {
  const parts: [string, boolean][] = [
    ["System prefix", true],
    ["Profile", true],
    ["Memories", true],
    ["Summary", false],
    ["Last 3 turns", false],
    ["This turn's facts", false],
    ["Question", false],
  ];
  return (
    <figure className="ar-strip">
      <ol>
        {parts.map(([label, cached]) => (
          <li key={label} data-cached={cached || undefined}>
            {label}
          </li>
        ))}
      </ol>
      <figcaption>The dark part is the stable prefix, reused from the KV cache; the rest follows in order.</figcaption>
    </figure>
  );
}

function Flow({ wide, still, playKey }: { wide: boolean; still: boolean; playKey: number }) {
  const stage = useRef<HTMLDivElement>(null);
  const seen = useInView(stage, { once: true, amount: 0.5 });
  // reduced motion rests on the whole path, lit; otherwise it waits for the scroll or Play
  const [step, setStep] = useState(still ? FLOW.length - 1 : -1);
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
    if (step >= FLOW.length - 1) {
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
    <section className="ar-chapter ar-flow" aria-labelledby="ar-h-flow" id="flow">
      <div className="ar-flow-head">
        <h2 id="ar-h-flow">{FLOW_GROUP.title}</h2>
        <p className="ar-lede">{FLOW_GROUP.lede}</p>
      </div>
      <div className="ar-stage" ref={stage}>
        <div className="ar-ask">
          <p className="ar-bubble">{SAMPLE.question}</p>
          <button type="button" className="ar-pill" onClick={playing ? () => setPlaying(false) : play}>
            {playing ? "Pause" : step >= FLOW.length - 1 ? "Play again" : "Play"}
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
              {i < FLOW.length - 1 && (
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
    </section>
  );
}

export default function Architecture() {
  const still = useStill();
  const wide = useWide();
  const [playKey, setPlayKey] = useState(0);

  useEffect(() => {
    document.title = "How Finance Agent works";
  }, []);

  const playFromHero = () => {
    setPlayKey((k) => k + 1);
    document.getElementById("flow")?.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "start" });
  };

  return (
    <MotionConfig reducedMotion="user">
      <div className="ar">
        <header className="ar-bar">
          <a className="ar-word" href="/">
            Finance Agent
          </a>
          <nav aria-label="Site">
            <a href="/app">Demo app</a>
            <a href={REPO}>Source</a>
          </nav>
        </header>
        <main>
          <section className="ar-hero" aria-labelledby="ar-title">
            <div className="ar-hero-text">
              <h1 id="ar-title">How the Assistant answers</h1>
              <p className="ar-sub">
                Code plans the turn, the Engine computes every figure, and one model call writes the words. Open any
                part to see the decision, why it was made, and what was turned down.
              </p>
              <div className="ar-hero-foot">
                <button type="button" className="ar-pill ar-pill-big" onClick={playFromHero}>
                  Play a question
                </button>
                <ul className="ar-key" aria-label="Status key">
                  {(Object.keys(STATUS_LABEL) as Status[]).map((s) => (
                    <li key={s}>
                      <StatusTag status={s} />
                    </li>
                  ))}
                </ul>
              </div>
            </div>
            <nav className="ar-toc" aria-label="On this page">
              <ul>
                {GROUPS.map((g) => (
                  <li key={g.id}>
                    <a href={g.id === "flow" ? "#flow" : `#ar-${g.id}`}>{g.title}</a>
                    <span>{NODES.filter((n) => n.group === g.id).length} parts</span>
                  </li>
                ))}
              </ul>
            </nav>
          </section>
          <Flow wide={wide} still={still} playKey={playKey} />
          {CHAPTERS.map((g) => (
            <Chapter key={`${g.id}-${wide}`} group={g} wide={wide} />
          ))}
        </main>
        <footer className="ar-foot">
          <p>
            The hosted demo runs synthetic ledgers with recorded replies: no model, no cloud and no real data. Everything
            above describes the local app, and each part links to the code and decision records behind it.
          </p>
          <nav aria-label="Footer">
            <a href="/">Home</a>
            <a href="/app">Demo app</a>
            <a href={`${REPO}/tree/main/docs/adr`}>Decision records</a>
          </nav>
          <p className="ar-foot-name" aria-hidden="true">
            Finance Agent
          </p>
        </footer>
      </div>
    </MotionConfig>
  );
}
