import { motion, type MotionValue, useScroll, useTransform } from "motion/react";
import { type ReactNode, useRef, useState } from "react";
import { type Category, CategoryChip } from "@/components/CategoryChip";
import { Money } from "@/components/Money";
import { Field, Material } from "../fx";
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
      <p className="st-source">From {ANSWER.tool}, run on your ledger</p>
    </>
  );
}

export const PILE: { title: string; body: () => ReactNode }[] = [
  { title: "See the month at a glance.", body: OverviewCard },
  { title: "Confirm only what it is unsure of.", body: ReviewCard },
  { title: "Ask, and get figures it calculated.", body: AnswerCard },
].slice(0, MAX_LAYERS);

const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
const stage = (v: number, [a, b]: [number, number]) => (b <= a ? 1 : ease(Math.min(1, Math.max(0, (v - a) / (b - a)))));

/** Where card i comes in, as a share of the pinned scroll: the first is there when the pin
    starts, the next two slide up over it in turn, and the last holds a long beat before the
    pin lets go, so the release is the whole stage moving on together and nothing jumps. */
export const ENTER: [number, number][] = [
  [0, 0],
  [0.14, 0.4],
  [0.48, 0.74],
];

/** How many cards have landed on top of card i (0 to 2, continuous). */
const over = (i: number, v: number) => ENTER.slice(i + 1).reduce((n, e) => n + stage(v, e), 0);

/** One card on the pinned stage. It slides up from below the stage as its stage plays, then
    recedes a step (up, smaller, dimmed) for each card that lands on top of it. */
function Panel({ i, p, children }: { i: number; p: MotionValue<number>; children: ReactNode }) {
  const transform = useTransform(p, (v) => {
    const k = stage(v, ENTER[i] ?? [0, 0]);
    const o = over(i, v);
    // from below the stage's bottom edge, then up a step (in % of its own height) per card above
    return `translate3d(0, calc(${((1 - k) * 80).toFixed(3)}svh - ${(o * 4).toFixed(3)}%), 0) scale(${(1 - o * 0.05).toFixed(4)})`;
  });
  const veil = useTransform(p, (v) => over(i, v) * 0.3);
  return (
    <motion.div className="st-panel" data-layer={i} style={{ transform, zIndex: i + 1 }}>
      <Material className="st-card-host" max={3}>
        <article className="st-card" data-ui="">
          {children}
          <motion.span className="st-card-veil" style={{ opacity: veil }} aria-hidden="true" />
        </article>
      </Material>
    </motion.div>
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

/** Desktop: one screen of stage, pinned while the three cards play their stages over it. */
function Pinned({ title }: { title: ReactNode }) {
  const track = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({ target: track, offset: ["start start", "end end"] });
  return (
    <div ref={track} className="st-pile-track">
      <div className="st-pile-stage">
        <div className="lp-wrap st-pile-grid">
          {title}
          <div className="st-deck">
            {PILE.map(({ title: t, body: Body }, i) => (
              <Panel key={t} i={i} p={scrollYProgress}>
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
