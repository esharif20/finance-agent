import { motion, useScroll, useTransform } from "motion/react";
import { type ReactNode, type RefObject, useRef, useState } from "react";
import { type Category, CategoryChip } from "@/components/CategoryChip";
import { Money } from "@/components/Money";
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

/** A card that sinks back (up 14px, to 0.93, dimmed to 0.55) while the next one slides over it. */
function Sinking({ next, children }: { next: RefObject<HTMLDivElement | null>; children: ReactNode }) {
  const { scrollYProgress } = useScroll({ target: next, offset: ["start end", "start start"] });
  const y = useTransform(scrollYProgress, [0, 1], [0, -14]);
  const scale = useTransform(scrollYProgress, [0, 1], [1, 0.93]);
  // dims to 0.55 under a ground-coloured veil, so the card stays opaque over the one beneath it
  const veil = useTransform(scrollYProgress, [0, 1], [0, 0.45]);
  return (
    <motion.article className="st-card" data-ui="" style={{ y, scale }}>
      {children}
      <motion.span className="st-card-veil" style={{ opacity: veil }} aria-hidden="true" />
    </motion.article>
  );
}

export function Pile({ still }: { still: boolean }) {
  const slots = [useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null)];
  return (
    <section className="st-pile" data-still={still ? "" : undefined} aria-labelledby="st-pile-title">
      <div className="lp-wrap st-pile-grid">
        <div className="st-pile-title">
          <h2 id="st-pile-title" className="lp-h2">
            The app itself.
          </h2>
          <p className="st-pile-note">The real screens, running on a made-up ledger. Pick a category in the queue: it is the app’s own editor.</p>
        </div>
        <div className="st-pile-cards">
          {PILE.map(({ title, body: Body }, i) => {
            const next = slots[i + 1];
            const content = (
              <>
                <h3 className="lp-h3 st-card-title">{title}</h3>
                <Body />
              </>
            );
            return (
              <div key={title} ref={slots[i]} className="st-slot" data-layer={i} style={{ zIndex: i + 1 }}>
                {still || !next || i === PILE.length - 1 ? (
                  <article className="st-card" data-ui="">{content}</article>
                ) : (
                  <Sinking next={next}>{content}</Sinking>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
