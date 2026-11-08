import { motion, useScroll, useSpring, useTransform } from "motion/react";
import { useRef } from "react";
import { formatMoney } from "@/lib/money";
import { Field, useStill } from "./fx";
import { ANSWER, OVERVIEW } from "./story/story.fixture";

// Every statement here is from docs/AI_DESIGN.md, docs/ARCHITECTURE.md and docs/THREAT_MODEL.md.
const QUESTION = `How much did I spend on eating out in ${OVERVIEW.month}?`;
const REPLY = `${formatMoney(ANSWER.total)}, across ${ANSWER.count} payments.`;

const PATH = [
  {
    title: "It reads the question in code first",
    body: "A plain spending question is parsed by rules into one calculation and answered from a template. No model is called at all.",
    tag: "0 model calls",
  },
  {
    title: "It looks the figures up",
    body: "Anything else: before the model sees the question, the app fetches this month, last month and the Category you named from your ledger.",
    tag: "Still 0 model calls",
  },
  {
    title: "One model call words the answer",
    body: "A small model running on your Mac writes the sentence from those figures. It can ask for one more lookup if it needs to.",
    tag: "1 call, on your Mac",
  },
  {
    title: "Every figure is checked",
    body: "Each amount, percentage and date in the reply must match a figure the lookups returned. If one doesn’t, it tries once more, then shows a plain table instead.",
    tag: "Checked before you see it",
  },
  {
    title: "The answer shows its working",
    body: "The reply reaches the screen only after the check, with the transactions it came from attached as evidence.",
    tag: REPLY,
  },
];

const HOME = [
  { name: "Your ledger", note: "One encrypted database file" },
  { name: "The keys", note: "In the macOS Keychain" },
  { name: "The model", note: "Runs locally through Ollama" },
];

const OUT = [
  { name: "The local model", note: "Your own Mac (localhost), nowhere else", when: "Always" },
  { name: "Your bank feed", note: "One pinned, read-only aggregator host", when: "If you connect a bank" },
  { name: "A cloud model", note: "Labels like “Amount 1”, or the news topics you tick. Never your figures", when: "Only if you opt in" },
];

const BACKGROUND = [
  { name: "Sync", note: "Every 6 hours a background job fetches new transactions from your bank feed, then backs up." },
  { name: "Review", note: "New payments it isn’t sure about wait in one short queue." },
  { name: "Briefings", note: "A weekly one on your money, worked out on your Mac. A daily news one on topics you tick, if you turn it on." },
  { name: "Memory", note: "Things you tell it to remember, which you can read and delete." },
];

function Step({ i, s, still }: { i: number; s: (typeof PATH)[number]; still: boolean }) {
  return (
    <motion.li
      className="hw-step"
      initial={still ? false : { opacity: 0.28 }}
      whileInView={{ opacity: 1 }}
      viewport={{ margin: "-45% 0px -45% 0px" }}
      transition={{ duration: 0.3 }}
    >
      <span className="hw-num" aria-hidden="true">
        {i + 1}
      </span>
      <h3 className="hw-step-title">{s.title}</h3>
      <p>{s.body}</p>
      <span className="hw-tag">{s.tag}</span>
    </motion.li>
  );
}

/** How a question becomes an answer, scrolled through one step at a time, then what stays on the
    Mac, the only places anything goes, and what runs while you're away. */
export function HowItWorks() {
  const still = useStill();
  const list = useRef<HTMLOListElement>(null);
  const { scrollYProgress } = useScroll({ target: list, offset: ["start 60%", "end 50%"] });
  const fill = useSpring(scrollYProgress, { stiffness: 120, damping: 30, restDelta: 0.001 });
  const rail = useTransform(fill, (v) => `scaleY(${v})`);
  return (
    <section id="how" className="hw" aria-labelledby="hw-title">
      <Field tone="paper" />
      <div className="lp-wrap">
        <div className="hw-top">
        <header className="hw-head">
          <h2 id="hw-title" className="lp-h2">
            {"How a question "}
            <br />
            gets its answer.
          </h2>
          <p className="hw-q">
            <span>{QUESTION}</span>
          </p>
        </header>
        <div className="hw-path">
          <span className="hw-rail" aria-hidden="true">
            <motion.span style={still ? undefined : { transform: rail }} />
          </span>
          <ol ref={list} className="hw-steps">
            {PATH.map((s, i) => (
              <Step key={s.title} i={i} s={s} still={still} />
            ))}
          </ol>
        </div>

        </div>
        <div className="hw-map">
          <div className="hw-mac">
            <h3 className="lp-h3">Your Mac</h3>
            <ul className="hw-home">
              {HOME.map((h) => (
                <li key={h.name}>
                  <strong>{h.name}</strong>
                  <span>{h.note}</span>
                </li>
              ))}
            </ul>
            <p className="hw-lock">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <rect x="5" y="11" width="14" height="10" rx="2" />
                <path d="M8 11V8a4 4 0 0 1 8 0v3" />
              </svg>
              It never moves money. A change it suggests waits for you to press Confirm.
            </p>
          </div>
          <div className="hw-out">
            <h3 className="lp-h3">The only three places anything goes</h3>
            <ul>
              {OUT.map((o) => (
                <li key={o.name}>
                  <span className="hw-when">{o.when}</span>
                  <strong>{o.name}</strong>
                  <span>{o.note}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div className="hw-bg">
          <h3 className="lp-h3">While you’re away</h3>
          <ul>
            {BACKGROUND.map((b) => (
              <li key={b.name}>
                <strong>{b.name}</strong>
                <span>{b.note}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
