import { MotionConfig } from "motion/react";
import { type ComponentType, useEffect, useState } from "react";
import ArchMap from "./ArchMap";
import { EgressFigure, ModelsFigure } from "./Figures";
import Flow from "./Flow";
import { GROUPS, type Group, NODES, REPO, STATUS_LABEL, type Status } from "./nodes";
import { Detail, NodeButton, StatusTag, useMedia, useStill } from "./parts";
import WhatsNext from "./WhatsNext";
import "./architecture.css";

const [, ...CHAPTERS] = GROUPS as [Group, ...Group[]];

const FIGURE: Partial<Record<Group["id"], ComponentType>> = {
  models: ModelsFigure,
  security: EgressFigure,
};

/** One chapter: its nodes, and the open node's detail beside them (wide) or under the node (phone). */
function Chapter({ group, wide }: { group: Group; wide: boolean }) {
  const nodes = NODES.filter((n) => n.group === group.id);
  const [open, setOpen] = useState<string | null>(wide ? (nodes[0]?.id ?? null) : null);
  const shown = nodes.find((n) => n.id === open);
  const toggle = (id: string) => setOpen((o) => (o === id && !wide ? null : id));
  const Fig = FIGURE[group.id];
  return (
    <section className={`ar-chapter ar-${group.id}`} id={`ar-${group.id}`} aria-labelledby={`ar-h-${group.id}`}>
      <div className="ar-chapter-text">
        <h2 id={`ar-h-${group.id}`}>{group.title}</h2>
        <p className="ar-lede">{group.lede}</p>
        {group.id === "context" && <PromptStrip />}
        {Fig && <Fig />}
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

export default function Architecture() {
  const still = useStill();
  const wide = useMedia("(min-width: 900px)");
  const coarse = useMedia("(pointer: coarse)");
  // the scroll scene needs room, a fine pointer and motion; everything else gets the stepped path
  const scene = wide && !coarse && !still;
  const [playKey, setPlayKey] = useState(0);

  useEffect(() => {
    document.title = "How Finance Agent works";
  }, []);

  const playFromHero = () => {
    setPlayKey((k) => k + 1);
    if (!scene) document.getElementById("flow")?.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "start" });
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
            <h1 id="ar-title">How the Assistant answers</h1>
            <div className="ar-hero-text">
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
          </section>
          <ArchMap wide={wide} />
          <Flow scene={scene} wide={wide} still={still} playKey={playKey} />
          {CHAPTERS.map((g) => (
            <Chapter key={`${g.id}-${wide}`} group={g} wide={wide} />
          ))}
          <WhatsNext />
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
