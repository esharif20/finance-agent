import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { motion } from "motion/react";
import { type PointerEvent, type ReactNode, useRef, useState } from "react";
import { type Category, CategoryChip } from "@/components/CategoryChip";
import { Money } from "@/components/Money";
import { Field, finePointer } from "../fx";
import { ANSWER, GROUPS, OVERVIEW, REVIEW } from "./story.fixture";

/** #76: the pile shows at most this many frosted layers. */
export const MAX_LAYERS = 3;

function OverviewCard() {
  return (
    <>
      <p className="st-card-kicker">Overview · {OVERVIEW.month}</p>
      <p className="st-card-figure">
        <Money value={OVERVIEW.spent} />
      </p>
      <p className="st-card-note">
        spent, against <Money value={OVERVIEW.lastMonth} /> last month
      </p>
      <ul className="st-bars">
        {OVERVIEW.top.map((c) => (
          <li key={c.name}>
            <span>{c.name}</span>
            <Money value={c.amount} />
            <i style={{ "--share": c.share } as React.CSSProperties} aria-hidden="true" />
          </li>
        ))}
      </ul>
    </>
  );
}

function ReviewCard() {
  // the chips are the app's own editor; a pick here only changes this card
  const [cats, setCats] = useState<Category[]>(() => REVIEW.map((r) => r.category));
  return (
    <>
      <p className="st-card-kicker">Review Queue · {cats.filter((c) => c[0] === "To review").length} to confirm</p>
      <ul className="st-rows">
        {REVIEW.map((r, i) => (
          <li key={r.merchant}>
            <span className="st-row-name">{r.merchant}</span>
            <CategoryChip
              value={cats[i] ?? null}
              groups={GROUPS}
              merchant={r.merchant}
              onChange={(c) => setCats((all) => all.map((x, j) => (j === i ? c : x)))}
            />
            <Money value={r.amount} className="st-row-amount" />
          </li>
        ))}
      </ul>
    </>
  );
}

function AnswerCard() {
  return (
    <>
      <p className="st-card-kicker">Assistant</p>
      <p className="st-ask">{ANSWER.question}</p>
      <p className="st-reply">
        You spent <Money value={ANSWER.total} /> on eating out across {ANSWER.count} payments, up from{" "}
        <Money value={ANSWER.previous} /> in September.
      </p>
      <div className="st-tool" aria-label={`From ${ANSWER.tool}, run on your ledger`}>
        <code>
          {ANSWER.tool}(category=&quot;Eating out&quot;, month=&quot;{OVERVIEW.month}&quot;)
        </code>
        <dl className="st-months">
          <div style={{ "--share": ANSWER.previous.minor / ANSWER.total.minor } as React.CSSProperties}>
            <dt>September</dt>
            <dd>
              <Money value={ANSWER.previous} />
            </dd>
            <i aria-hidden="true" />
          </div>
          <div style={{ "--share": 1 } as React.CSSProperties}>
            <dt>{OVERVIEW.month}</dt>
            <dd>
              <Money value={ANSWER.total} />
            </dd>
            <i aria-hidden="true" />
          </div>
        </dl>
      </div>
      <p className="st-source">From {ANSWER.tool}, run on your ledger</p>
    </>
  );
}

export const PILE: { title: string; body: () => ReactNode }[] = [
  { title: "See the month at a glance.", body: OverviewCard },
  { title: "Confirm only what it is unsure of.", body: ReviewCard },
  { title: "Ask, and get figures it calculated.", body: AnswerCard },
].slice(0, MAX_LAYERS);

/** One card of the deck. Pointer effects after React Bits (github.com/DavidHDev/react-bits, MIT):
    SpotlightCard (a soft light that follows the cursor), TiltedCard (a few degrees of tilt toward
    it, eased with gsap.quickTo) and an animated border that runs round the card in front. Fine
    pointers only; GSAP's timeline in Pinned moves the panel, these move only the card inside it. */
function Panel({ i, children }: { i: number; children: ReactNode }) {
  const card = useRef<HTMLElement>(null);
  const tilt = useRef<{ x: (v: number) => void; y: (v: number) => void } | null>(null);
  useGSAP(() => {
    if (!card.current || !finePointer()) return;
    tilt.current = {
      x: gsap.quickTo(card.current, "rotationY", { duration: 0.5, ease: "power3.out" }),
      y: gsap.quickTo(card.current, "rotationX", { duration: 0.5, ease: "power3.out" }),
    };
  });
  const move = (e: PointerEvent<HTMLElement>) => {
    const el = card.current;
    if (!el || e.pointerType !== "mouse") return;
    const b = el.getBoundingClientRect();
    const x = (e.clientX - b.left) / b.width;
    const y = (e.clientY - b.top) / b.height;
    el.style.setProperty("--mx", `${(x * 100).toFixed(1)}%`);
    el.style.setProperty("--my", `${(y * 100).toFixed(1)}%`);
    tilt.current?.x((x - 0.5) * 7);
    tilt.current?.y((0.5 - y) * 5);
  };
  const leave = () => {
    tilt.current?.x(0);
    tilt.current?.y(0);
  };
  return (
    <div className="st-panel" data-layer={i} style={{ zIndex: i + 1 }}>
      <article ref={card} className="st-card" data-ui="" onPointerMove={move} onPointerLeave={leave}>
        <span className="st-card-spot" aria-hidden="true" />
        {children}
        <span className="st-card-veil" aria-hidden="true" />
      </article>
    </div>
  );
}

/** Phones, tablets and reduced motion: no pin, the cards in a column, each rising once into view. */
function Stacked({ still }: { still: boolean }) {
  return (
    <div className="st-pile-stack">
      {PILE.map(({ title, body: Body }, i) => {
        const card = (
          <article className="st-card" data-ui="">
            <h3 className="lp-h3 st-card-title">{title}</h3>
            <Body />
          </article>
        );
        return still ? (
          <div key={title} className="st-panel" data-layer={i}>
            {card}
          </div>
        ) : (
          <motion.div
            key={title}
            className="st-panel"
            data-layer={i}
            initial={{ opacity: 0, y: 32 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "0px 0px -12% 0px" }}
            transition={{ duration: 0.7, ease: [0.23, 1, 0.32, 1] }}
          >
            {card}
          </motion.div>
        );
      })}
    </div>
  );
}

function compact() {
  return typeof window.matchMedia === "function" && window.matchMedia("(max-width: 899.98px), (pointer: coarse)").matches;
}

/** Desktop: one screen of stage, pinned (CSS sticky) while a GSAP timeline scrubbed by
    ScrollTrigger deals the next card over the last: it rises from below, tipped back, and lands
    flat while the one beneath steps back and dims. The step list on the left follows. */
function Pinned({ title }: { title: ReactNode }) {
  const track = useRef<HTMLDivElement>(null);
  const [step, setStep] = useState(0);
  useGSAP(
    () => {
      gsap.registerPlugin(ScrollTrigger);
      const q = gsap.utils.selector(track);
      const panels = q(".st-deck > .st-panel");
      const veils = q(".st-deck .st-card-veil");
      gsap.set(panels.slice(1), { yPercent: 118, rotationX: -18, transformPerspective: 1400 });
      gsap.set(veils, { opacity: 0 });
      const tl = gsap.timeline({
        defaults: { ease: "power2.inOut", duration: 1 },
        scrollTrigger: {
          trigger: track.current,
          start: "top top",
          end: "bottom bottom",
          scrub: 0.6,
          onUpdate: (st) => setStep(st.progress < 0.3 ? 0 : st.progress < 0.68 ? 1 : 2),
        },
      });
      tl.to(q(".st-steps-rail i"), { scaleY: 1, ease: "none", duration: 3.4 }, 0);
      for (let k = 1; k < panels.length; k++) {
        const at = 0.2 + (k - 1) * 1.3;
        tl.to(panels[k] ?? [], { yPercent: 0, rotationX: 0, ease: "power3.out" }, at);
        for (let j = 0; j < k; j++) {
          const depth = k - j;
          tl.to(panels[j] ?? [], { yPercent: -5 * depth, scale: 1 - 0.05 * depth }, at);
          tl.to(veils[j] ?? [], { opacity: 0.22 * depth }, at);
        }
      }
      tl.to({}, { duration: 0.6 }); // the last card holds before the pin lets go
    },
    { scope: track },
  );
  return (
    <div ref={track} className="st-pile-track">
      <div className="st-pile-stage">
        <div className="lp-wrap st-pile-grid">
          <div className="st-pile-side">
            {title}
            <ol className="st-steps" aria-hidden="true">
              <span className="st-steps-rail">
                <i />
              </span>
              {PILE.map((c, i) => (
                <li key={c.title} data-on={step === i ? "" : undefined} data-done={step > i ? "" : undefined}>
                  {c.title}
                </li>
              ))}
            </ol>
          </div>
          <div className="st-deck" data-step={step}>
            {PILE.map(({ title: t, body: Body }, i) => (
              <Panel key={t} i={i}>
                <h3 className="lp-h3 st-card-title">{t}</h3>
                <Body />
              </Panel>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export function Pile({ still }: { still: boolean }) {
  const [stack] = useState(() => still || compact());
  const title = (
    <div className="st-pile-title">
      <h2 id="st-pile-title" className="lp-h2">
        The app itself.
      </h2>
      <p className="st-pile-note">The real screens, running on a made-up ledger. Pick a category in the queue: it is the app’s own editor.</p>
    </div>
  );
  return (
    <section className="st-pile" data-mode={stack ? "stack" : "pin"} data-still={still ? "" : undefined} aria-labelledby="st-pile-title">
      <Field tone="night" />
      {stack ? (
        <div className="lp-wrap st-pile-grid">
          {title}
          <Stacked still={still} />
        </div>
      ) : (
        <Pinned title={title} />
      )}
    </section>
  );
}
