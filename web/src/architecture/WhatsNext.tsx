/* The page's close: what comes next, each item marked Planned (it has an issue) or Exploring (an
   idea, no issue yet). Nothing here exists today. */
import { motion } from "motion/react";
import type { ReactNode } from "react";
import { REPO } from "./nodes";
import { EASE } from "./parts";

type Kind = "planned" | "exploring";
interface Item {
  title: string;
  text: string;
  kind: Kind;
  issues?: number[];
  /** a 24 x 24 stroke path, for the idea tiles */
  glyph?: string;
}
interface Next {
  id: string;
  title: string;
  lede: string;
  glyph: ReactNode;
  items: Item[];
}

const G = { fill: "none", stroke: "currentColor", strokeWidth: 2.5, strokeLinecap: "round", strokeLinejoin: "round" } as const;

export const NEXT: Next[] = [
  {
    id: "context",
    title: "It knows you before you ask",
    lede: "The L0 profile ships today. Questions should hit summaries first, with one model call at most.",
    glyph: (
      <svg viewBox="0 0 32 32" {...G}>
        <circle cx="16" cy="16" r="13" />
        <circle cx="16" cy="16" r="7.5" />
        <circle cx="16" cy="16" r="2.5" fill="currentColor" />
      </svg>
    ),
    items: [
      {
        title: "L1 summaries and the L2 meaning index",
        text: "Monthly and quarterly summaries, and a meaning index over Merchants, Categories and notes, so most answers need no Ledger scan.",
        kind: "planned",
        issues: [327],
      },
      {
        title: "Conversation memory you can edit",
        text: "Follow-ups like \"and the month before?\" resolved by code; confirmed Memories shown, and deletable.",
        kind: "planned",
        issues: [317],
      },
      {
        title: "\"Why this answer\" trace",
        text: "Open any reply's plan: which tools ran, which figures, and the grounding check.",
        kind: "exploring",
      },
    ],
  },
  {
    id: "learning",
    title: "It learns you",
    lede: "Each model ships only if it beats its baseline on the evaluation set.",
    glyph: (
      <svg viewBox="0 0 32 32" {...G}>
        <path d="M26 16a10 10 0 1 1-3-7.1" />
        <path d="M24 3.5v6h-6" />
      </svg>
    ),
    items: [
      {
        title: "The bandit out of shadow",
        text: "Chooses which Insight comes first, gated by off-policy evaluation; rewards are acceptances only, never engagement.",
        kind: "planned",
        issues: [272],
      },
      {
        title: "Preference learning",
        text: "Pairwise \"which is more useful?\" answers tune the ranking, Bradley-Terry.",
        kind: "planned",
        issues: [255],
      },
      {
        title: "Small local fine-tunes",
        text: "A LoRA for the planner and a categoriser head, trained with MLX on the Mac from your own Corrections.",
        kind: "planned",
        issues: [327],
      },
      {
        title: "Learned router",
        text: "No call, one call or in depth, learned from labelled questions, so simple ones stay instant.",
        kind: "planned",
        issues: [284, 285],
      },
    ],
  },
  {
    id: "news",
    title: "News that means something to you",
    lede: "Today's briefing sends only topic names, mixed with decoys. Next, code joins the news to your Ledger on the Mac.",
    glyph: (
      <svg viewBox="0 0 32 32" {...G}>
        <rect x="4" y="5" width="20" height="22" rx="3" />
        <path d="M24 11h4v12a4 4 0 0 1-4 4" />
        <path d="M9 11h10M9 16h10M9 21h6" />
      </svg>
    ),
    items: [
      {
        title: "Price-change impact",
        text: "A subscription's announced rise becomes your yearly cost, computed by the Engine from your own payments.",
        kind: "exploring",
      },
      {
        title: "Rates and the price cap",
        text: "A base-rate change becomes its effect on your savings and overdraft; a new energy cap goes into the bills forecast.",
        kind: "exploring",
      },
      {
        title: "Your own inflation rate",
        text: "Your basket of Categories weighted against the public CPI series. The series comes in; your weights never leave.",
        kind: "exploring",
      },
    ],
  },
  {
    id: "ideas",
    title: "New ideas",
    lede: "Each would keep the same rule: code computes, the model only words it.",
    glyph: (
      <svg viewBox="0 0 32 32" {...G}>
        <path d="M16 3v6M16 23v6M3 16h6M23 16h6M7 7l4 4M21 21l4 4M25 7l-4 4M11 21l-4 4" />
      </svg>
    ),
    items: [
      { title: "Forecast with a range", text: "Cash flow to payday with confidence bands, and a warning before an overdraft.", kind: "exploring", glyph: "M3 17l5-5 4 3 8-8M3 21h18" },
      { title: "Price creep and duplicates", text: "Notices when a recurring payment rises, or two services overlap.", kind: "exploring", glyph: "M4 18h4V9H4zM10 18h4V6h-4zM16 18h4V3h-4z" },
      { title: "Weekly \"what changed\"", text: "New Merchants, unusual payments and recurring changes, every figure from the Engine.", kind: "exploring", glyph: "M4 4h16v16H4zM4 9h16M9 4v5" },
      { title: "What-if in chat", text: "\"If I halve takeaways\" gives the saving and the date a goal is reached.", kind: "exploring", glyph: "M4 5h16v10H9l-5 4z" },
      { title: "Goal coach", text: "Progress checked against actual spending, with nudges you can switch off.", kind: "exploring", glyph: "M12 3a9 9 0 1 0 9 9M12 8a4 4 0 1 0 4 4M12 12l8-8" },
      { title: "At-the-till notes", text: "A Shortcut and local speech-to-text add purpose notes and splits as an overlay.", kind: "exploring", issues: [80], glyph: "M9 3h6v10H9zM5 11a7 7 0 0 0 14 0M12 18v3" },
      { title: "Why is this month higher?", text: "The gap broken down by Category and Merchant.", kind: "exploring", glyph: "M5 20V10M12 20V4M19 20v-7M3 20h18" },
    ],
  },
];

const RULES = [
  "Nothing here moves money: no payments or transfers, ever.",
  "The model never computes a figure (ADR 0007).",
  "Any new network host needs its own decision record first.",
];

const LABEL: Record<Kind, string> = { planned: "Planned", exploring: "Exploring" };

function KindTag({ kind }: { kind: Kind }) {
  return (
    <span className="ar-kind" data-kind={kind}>
      <span className="ar-kind-mark" aria-hidden="true" />
      {LABEL[kind]}
    </span>
  );
}

function Issues({ issues }: { issues?: number[] }) {
  return (
    <>
      {issues?.map((n) => (
        <a key={n} className="ar-issue" href={`${REPO}/issues/${n}`}>
          #{n}
        </a>
      ))}
    </>
  );
}

const rise = (i: number) => ({
  initial: { opacity: 0, y: 20 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, amount: 0.2 },
  transition: { duration: 0.5, delay: i * 0.06, ease: EASE },
});

export default function WhatsNext() {
  const groups = NEXT.filter((g) => g.id !== "ideas");
  const ideas = NEXT.find((g) => g.id === "ideas");
  return (
    <section className="ar-chapter ar-next" id="ar-next" aria-labelledby="ar-h-next">
      <div className="ar-chapter-text">
        <h2 id="ar-h-next">What's next</h2>
        <p className="ar-lede">
          Planned items have an issue; Exploring items are ideas without one. None of them exists yet.
        </p>
      </div>
      <div className="ar-next-grid">
        {groups.map((g, i) => (
          <motion.article key={g.id} className="ar-next-card" aria-labelledby={`ar-h-n-${g.id}`} {...rise(i)}>
            <div className="ar-next-head">
              <span className="ar-glyph" aria-hidden="true">
                {g.glyph}
              </span>
              <h3 id={`ar-h-n-${g.id}`}>{g.title}</h3>
            </div>
            <p className="ar-next-lede">{g.lede}</p>
            <ul>
              {g.items.map((it) => (
                <li key={it.title}>
                  <div className="ar-next-item-head">
                    <h4>{it.title}</h4>
                    <KindTag kind={it.kind} />
                  </div>
                  <p>
                    {it.text}
                    <Issues issues={it.issues} />
                  </p>
                </li>
              ))}
            </ul>
          </motion.article>
        ))}
      </div>
      {ideas && (
        <div className="ar-ideas">
          <div className="ar-next-head">
            <span className="ar-glyph" aria-hidden="true">
              {ideas.glyph}
            </span>
            <h3 id="ar-h-n-ideas">{ideas.title}</h3>
          </div>
          <p className="ar-next-lede">{ideas.lede}</p>
          <ul className="ar-idea-grid" aria-labelledby="ar-h-n-ideas">
            {ideas.items.map((it, i) => (
              <motion.li key={it.title} className="ar-idea" {...rise(i % 4)}>
                {it.glyph && (
                  <svg viewBox="0 0 24 24" {...G} strokeWidth={2} aria-hidden="true">
                    <path d={it.glyph} />
                  </svg>
                )}
                <h4>{it.title}</h4>
                <p>
                  {it.text}
                  <Issues issues={it.issues} />
                </p>
                <KindTag kind={it.kind} />
              </motion.li>
            ))}
            <li className="ar-idea ar-idea-rules">
              <h4>What stays true</h4>
              <ul>
                {RULES.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </li>
          </ul>
        </div>
      )}
    </section>
  );
}
