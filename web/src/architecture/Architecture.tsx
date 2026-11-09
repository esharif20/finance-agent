import { MotionConfig } from "motion/react";
import Agent from "./Agent";
import { type ReactNode, useEffect, useState } from "react";
import { EvalChart, LearningChart, PromptStack, SECURITY_VISUAL, SystemChart, Tiers } from "./Diagrams";
import { EgressFigure, ModelsFigure } from "./Figures";
import Flow from "./Flow";
import { GROUPS, type Group, NODES, REPO, STATUS_LABEL, type Status } from "./nodes";
import { Detail, NodeButton, StatusTag, useMedia, useStill } from "./parts";
import WhatsNext from "./WhatsNext";
import Wayfinder from "./Wayfinder";
import { CountUp, Magnet, SplitWords } from "./Bits";
import { useSmoothScroll } from "./smooth";
import "./architecture.css";

const [, ...CHAPTERS] = GROUPS as [Group, ...Group[]];

/** The poster row under the hero: four facts that hold for the local app, each with its source. */
const FACTS: { n: number; label: string; meta: string; tone: string }[] = [
  { n: NODES.length, label: "parts, each with its decision and what was turned down", meta: "All chapters", tone: "mint" },
  { n: 2, label: "hosts outside the Mac: a read-only bank feed, and Gemini if you opt in", meta: "ADR 0013, 0016", tone: "cyan" },
  { n: 1, label: "retry for a reply that misquotes a figure, then a plain table", meta: "ADR 0007", tone: "lemon" },
  { n: 0, label: "tools that can pay or move money. Writes wait for your confirm", meta: "By design", tone: "orange" },
];

const FIGURE: Partial<Record<Group["id"], (live: boolean) => ReactNode>> = {
  knowledge: () => <Tiers />,
  models: () => <ModelsFigure />,
  context: () => <PromptStack />,
  learning: (live) => <LearningChart live={live} />,
  evals: (live) => <EvalChart live={live} />,
  security: () => <EgressFigure />,
};

/** One chapter: its nodes, and the open node's detail beside them (wide) or under the node (phone). */
function Chapter({ group, wide, live, index }: { group: Group; wide: boolean; live: boolean; index: number }) {
  const nodes = NODES.filter((n) => n.group === group.id);
  const [open, setOpen] = useState<string | null>(wide ? (nodes[0]?.id ?? null) : null);
  const shown = nodes.find((n) => n.id === open);
  const toggle = (id: string) => setOpen((o) => (o === id && !wide ? null : id));
  const visual = (id: string) => SECURITY_VISUAL[id]?.();
  return (
    <section className={`ar-chapter ar-${group.id}`} id={`ar-${group.id}`} aria-labelledby={`ar-h-${group.id}`}>
      <p className="ar-meta" aria-hidden="true">
        <span>
          {String(index).padStart(2, "0")} / {String(GROUPS.length).padStart(2, "0")}
        </span>
        <span>{nodes.length} parts</span>
      </p>
      <div className="ar-chapter-text">
        <h2 id={`ar-h-${group.id}`}>{group.title}</h2>
        <p className="ar-lede">{group.lede}</p>
        {FIGURE[group.id]?.(live)}
      </div>
      <div className="ar-chapter-body">
        <ul className="ar-nodes" data-layout={group.id}>
          {nodes.map((n) => (
            <li key={n.id}>
              <NodeButton node={n} open={open === n.id} onToggle={() => toggle(n.id)} />
              {!wide && open === n.id && <Detail node={n} id={`ar-d-${n.id}`} visual={visual(n.id)} />}
            </li>
          ))}
        </ul>
        {wide && shown && <Detail node={shown} id={`ar-d-${shown.id}`} visual={visual(shown.id)} />}
      </div>
    </section>
  );
}

export default function Architecture() {
  const still = useStill();
  const wide = useMedia("(min-width: 900px)");
  const coarse = useMedia("(pointer: coarse)");
  // motion that runs on its own needs room, a fine pointer and no reduced-motion preference
  const live = wide && !coarse && !still;
  const [playKey, setPlayKey] = useState(0);
  useSmoothScroll(live);

  useEffect(() => {
    document.title = "How Finance Agent works";
  }, []);

  const playFromHero = () => {
    setPlayKey((k) => k + 1);
    if (!live) document.getElementById("flow")?.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "start" });
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
            <SplitWords id="ar-title" text="How the Assistant answers" live={live} />
            <div className="ar-hero-text">
              <p className="ar-sub">
                Code plans the turn, the Engine computes every figure, and one model call writes the words. Open any
                part to see the decision, why it was made, and what was turned down.
              </p>
              <div className="ar-hero-foot">
                <Magnet live={live}>
                  <button type="button" className="ar-pill ar-pill-big" onClick={playFromHero}>
                    Play a question
                  </button>
                </Magnet>
                <ul className="ar-key" aria-label="Status key">
                  {(Object.keys(STATUS_LABEL) as Status[]).map((s) => (
                    <li key={s}>
                      <StatusTag status={s} />
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </section>
          <ul className="ar-facts" aria-label="At a glance">
            {FACTS.map((f) => (
              <li key={f.meta} className={`ar-fact ar-tone-${f.tone}`}>
                <span className="ar-fact-meta">{f.meta}</span>
                <CountUp to={f.n} live={live} />
                <span className="ar-fact-label">{f.label}</span>
              </li>
            ))}
          </ul>
          <section className="ar-system" aria-labelledby="ar-h-system">
            <div className="ar-system-head">
              <h2 id="ar-h-system">The whole system, on one Mac</h2>
              <p>
                The dashed line is the Mac. Two hosts sit outside it: a read-only bank feed, and Gemini, which only
                ever sees placeholders and only when you opt in. Point at a box to see what it does.
              </p>
            </div>
            <SystemChart wide={wide} live={live} />
            <nav className="ar-toc" id="ar-toc" aria-label="On this page">
              <ul>
                {GROUPS.map((g) => (
                  <li key={g.id}>
                    <a href={g.id === "flow" ? "#flow" : `#ar-${g.id}`}>
                      {g.title}
                      <span>{NODES.filter((n) => n.group === g.id).length}</span>
                    </a>
                  </li>
                ))}
                <li>
                  <a href="#ar-next">What's next</a>
                </li>
              </ul>
            </nav>
          </section>
          <Flow scene={live} wide={wide} still={still} playKey={playKey} />
          <Agent wide={wide} live={live} />
          {CHAPTERS.map((g, i) => (
            <Chapter key={`${g.id}-${wide}`} group={g} wide={wide} live={live} index={i + 2} />
          ))}
          <WhatsNext />
          <Wayfinder still={still} />
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
