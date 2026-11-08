import { AnimatePresence, motion } from "motion/react";
import { type ReactNode, useId, useState } from "react";
import { Money } from "@/components/Money";
import type { Money as MoneyValue } from "@/lib/money";
import { Field, Lines, Rise, useStill } from "./fx";
import { ANSWER, OVERVIEW } from "./story/story.fixture";

export const EXAMPLES = ["Eating out in October", "Groceries in October", "Bills this month", "Total spent in October"];

type Reply =
  | { kind: "category"; name: string; amount: MoneyValue }
  | { kind: "total" }
  | { kind: "none" };

// Words a visitor might type for each top-level Category in the fixture.
const WORDS: Record<string, string[]> = {
  "Eating out": ["eat", "cafe", "café", "restaurant", "takeaway", "coffee", "dining"],
  Groceries: ["grocer", "supermarket", "food shop"],
  Bills: ["bill", "energy", "phone", "utilit"],
  Transport: ["transport", "train", "rail", "fuel", "travel"],
};

/** Matches a question to a figure the fixture already holds. Never a made-up figure: anything else
    gets the honest answer that it cannot say. */
export function reply(question: string): Reply {
  const q = question.toLowerCase();
  for (const c of OVERVIEW.top) if (WORDS[c.name]?.some((w) => q.includes(w))) return { kind: "category", name: c.name, amount: c.amount };
  if (/\b(spen[dt]|total|everything|all)\b/.test(q)) return { kind: "total" };
  return { kind: "none" };
}

function Answer({ r }: { r: Reply }) {
  if (r.kind === "total")
    return (
      <>
        <p className="lp-answer-text">
          You spent <Money value={OVERVIEW.spent} /> in {OVERVIEW.month}, against <Money value={OVERVIEW.lastMonth} /> the month
          before.
        </p>
        <Evidence>
          The total is every {OVERVIEW.month} payment on the ledger added up, transfers between your own accounts left out. A question this simple is worked out in code, so no model is called.
        </Evidence>
      </>
    );
  if (r.kind === "category" && r.name === "Eating out")
    return (
      <>
        <p className="lp-answer-text">
          You spent <Money value={ANSWER.total} /> on eating out in {OVERVIEW.month} across {ANSWER.count} payments, up from{" "}
          <Money value={ANSWER.previous} /> in September.
        </p>
        <Evidence count={ANSWER.count}>
          The figure is the sum of the {ANSWER.count} eating-out payments in {OVERVIEW.month} on the ledger, checked against them before you see it. A question
          this simple is worked out in code, so no model is called.
        </Evidence>
      </>
    );
  if (r.kind === "category")
    return (
      <>
        <p className="lp-answer-text">
          You spent <Money value={r.amount} /> on {r.name.toLowerCase()} in {OVERVIEW.month}.
        </p>
        <Evidence>
          The figure is that Category’s {OVERVIEW.month} payments on the ledger added up, checked before you see it. A question this simple is worked
          out in code, so no model is called.
        </Evidence>
      </>
    );
  return (
    <>
      <p className="lp-answer-text">It can’t answer that from this ledger, so it says so instead of guessing.</p>
      <p className="lp-answer-source">Try groceries, bills, eating out or transport</p>
    </>
  );
}

/** How the figure was checked: a strip in the product's own words that opens on hover, tap or
    keyboard to say what it was checked against. */
function Evidence({ count, children }: { count?: number; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className="lp-evidence" data-open={open ? "" : undefined}>
      <button type="button" className="lp-evidence-strip" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}>
        <span>{count ? `Checked against ${count} transactions` : "Checked against your ledger"}</span>
        <span>No AI call</span>
        <span className="lp-evidence-more">How it was checked</span>
      </button>
      <p id={id} className="lp-evidence-body">
        {children}
      </p>
    </div>
  );
}

const SPECIAL = [
  { title: "Worked out in code first", body: "“Fuel”, “last month”: a plain spending question becomes one exact calculation, with no AI call." },
  { title: "Every figure checked", body: "Each amount in a reply must match your ledger, or it isn’t shown." },
  { title: "Follow-ups keep their place", body: "It keeps the last few turns, so “and September?” means what you meant." },
  { title: "It knows your usual month", body: "So it can tell you when a Category is running ahead, or a subscription went up." },
];

/** The live thing: ask the made-up ledger a question and read where the figure came from. */
export function Ask() {
  const still = useStill();
  const id = useId();
  const [q, setQ] = useState(EXAMPLES[0] ?? "");
  const r = reply(q);
  const key = r.kind === "category" ? r.name : r.kind;
  return (
    <section className="lp-ask lp-field" aria-labelledby="lp-ask-title">
      <Field tone="cyan" />
      <div className="lp-wrap">
        <h2 id="lp-ask-title" className="lp-h2">
          <Lines lines={["Ask anything.", "Every number is checked."]} still={still} />
        </h2>
        <Rise still={still} className="lp-ask-box">
          <label htmlFor={id} className="lp-sr">
            Ask about the made-up ledger
          </label>
          <div className="lp-ask-line" data-ui="">
            <input id={id} className="lp-ask-input" value={q} onChange={(e) => setQ(e.target.value)} autoComplete="off" spellCheck={false} />
          </div>
          <div className="lp-answer" aria-live="polite" data-ui="">
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={key}
                initial={still ? { opacity: 0 } : { opacity: 0, y: 10, filter: "blur(4px)" }}
                animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                exit={still ? { opacity: 0 } : { opacity: 0, y: -6, filter: "blur(4px)" }}
                transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
              >
                <Answer r={r} />
              </motion.div>
            </AnimatePresence>
          </div>
          <ul className="lp-examples" aria-label="Examples">
            {EXAMPLES.map((e) => (
              <li key={e}>
                <button type="button" className="lp-example" aria-pressed={q === e} onClick={() => setQ(e)}>
                  {e}
                </button>
              </li>
            ))}
          </ul>
          <ul className="lp-special">
            {SPECIAL.map((s) => (
              <li key={s.title}>
                <strong>{s.title}</strong>
                <span>{s.body}</span>
              </li>
            ))}
          </ul>
        </Rise>
      </div>
    </section>
  );
}
