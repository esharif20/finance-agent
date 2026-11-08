import { LayoutGroup, motion, useInView } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { formatMoney } from "@/lib/money";
import { ACCOUNTS } from "./accounts.fixture";
import { Material, Rise, useStill } from "./fx";
import { ANSWER, OVERVIEW } from "./story/story.fixture";

// ---- relevance: Beta(1 + yes, 1 + no) per kind of Insight (engine/ranking.py) ----

type Kind = { id: string; title: string; yes: number; no: number; tone: string };
const KINDS: Kind[] = [
  { id: "rise", title: "A subscription went up", yes: 3, no: 1, tone: "mint" },
  { id: "spike", title: "Eating out is running ahead of last month", yes: 1, no: 1, tone: "cyan" },
  { id: "renewal", title: "An annual renewal is due", yes: 1, no: 2, tone: "lemon" },
];

/** log Γ by Lanczos (g = 7), enough for a drawing of the curve. */
function lgamma(x: number): number {
  const c = [0.99999999999980993, 676.5203681218851, -1259.1392167224028, 771.32342877765313, -176.61502916214059, 12.507343278686905, -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7];
  if (x < 0.5) return Math.log(Math.PI / Math.sin(Math.PI * x)) - lgamma(1 - x);
  const y = x - 1;
  let a = c[0] ?? 1;
  const t = y + 7.5;
  for (let i = 1; i < 9; i++) a += (c[i] ?? 0) / (y + i);
  return 0.5 * Math.log(2 * Math.PI) + (y + 0.5) * Math.log(t) - t + Math.log(a);
}

export function betaPdf(x: number, a: number, b: number) {
  if (x <= 0 || x >= 1) return 0;
  return Math.exp((a - 1) * Math.log(x) + (b - 1) * Math.log(1 - x) + lgamma(a + b) - lgamma(a) - lgamma(b));
}

function curve(a: number, b: number, w: number, h: number, top: number) {
  const pts: string[] = [];
  for (let i = 0; i <= 80; i++) {
    const x = i / 80;
    pts.push(`${(x * w).toFixed(1)},${(h - (betaPdf(x, a, b) / top) * h).toFixed(1)}`);
  }
  return `M${pts.join(" L")}`;
}

function Relevance() {
  const [kinds, setKinds] = useState(KINDS);
  const answer = (id: string, useful: boolean) =>
    setKinds((ks) => ks.map((k) => (k.id === id ? { ...k, yes: k.yes + (useful ? 1 : 0), no: k.no + (useful ? 0 : 1) } : k)));
  const ranked = [...kinds].sort((a, b) => (b.yes + 1) / (b.yes + b.no + 2) - (a.yes + 1) / (a.yes + a.no + 2));
  const W = 320;
  const H = 120;
  const top = Math.max(...kinds.map((k) => betaPdf(Math.min(0.999, Math.max(0.001, (k.yes) / Math.max(1, k.yes + k.no))), k.yes + 1, k.no + 1)), 2.2);
  return (
    <div className="lp-learn-rel">
      <svg className="lp-learn-curves" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
        {kinds.map((k) => (
          <motion.path key={k.id} data-tone={k.tone} animate={{ d: curve(k.yes + 1, k.no + 1, W, H, top * 1.1) }} transition={{ type: "spring", stiffness: 120, damping: 20 }} />
        ))}
      </svg>
      <LayoutGroup>
        <ol className="lp-learn-cards">
          {ranked.map((k) => (
            <motion.li key={k.id} layout transition={{ type: "spring", stiffness: 260, damping: 28 }} data-tone={k.tone}>
              <span className="lp-learn-title">{k.title}</span>
              <span className="lp-learn-odds">
                {k.yes} useful, {k.no} dismissed
              </span>
              <span className="lp-learn-btns">
                <button type="button" onClick={() => answer(k.id, true)} aria-label={`Useful: ${k.title}`}>
                  Useful
                </button>
                <button type="button" onClick={() => answer(k.id, false)} aria-label={`Dismiss: ${k.title}`}>
                  Dismiss
                </button>
              </span>
            </motion.li>
          ))}
        </ol>
      </LayoutGroup>
    </div>
  );
}

// ---- preferences: Bradley-Terry strengths, MAP under N(0, 1) (engine/preferences.py) ----

const ITEMS = ["Eating out", "Subscriptions", "Transport", "Clothes", "Takeaways"];
type Duel = [number, number, number]; // kept, cut, count

/** MAP strengths by gradient ascent on the log posterior: concave, so it settles on one answer. */
export function strengths(duels: Duel[], n: number) {
  const s = new Array<number>(n).fill(0);
  for (let it = 0; it < 200; it++) {
    const g = s.map((v) => -v); // the prior's pull toward 0 (sd 1)
    for (const [w, l, c] of duels) {
      const p = 1 / (1 + Math.exp(-((s[w] ?? 0) - (s[l] ?? 0))));
      g[w] = (g[w] ?? 0) + c * (1 - p);
      g[l] = (g[l] ?? 0) - c * (1 - p);
    }
    for (let i = 0; i < n; i++) s[i] = (s[i] ?? 0) + 0.2 * (g[i] ?? 0);
  }
  return s;
}

function nextPair(s: number[], asked: Set<string>): [number, number] {
  let best: [number, number] = [0, 1];
  let score = -1;
  for (let a = 0; a < s.length; a++)
    for (let b = a + 1; b < s.length; b++) {
      if (asked.has(`${a}-${b}`) && asked.size < (s.length * (s.length - 1)) / 2) continue;
      const p = 1 / (1 + Math.exp(-((s[a] ?? 0) - (s[b] ?? 0))));
      const info = p * (1 - p); // the most uncertain pair teaches the most
      if (info > score) [score, best] = [info, [a, b]];
    }
  return best;
}

function Preferences() {
  const [duels, setDuels] = useState<Duel[]>([]);
  const s = useMemo(() => strengths(duels, ITEMS.length), [duels]);
  const asked = useMemo(() => new Set(duels.map(([w, l]) => `${Math.min(w, l)}-${Math.max(w, l)}`)), [duels]);
  const [a, b] = nextPair(s, asked);
  // "which would you cut first?": the item kept is the winner
  const cut = (gone: number, kept: number) => setDuels((d) => [...d, [kept, gone, 1]]);
  const order = ITEMS.map((name, i) => ({ name, i, s: s[i] ?? 0 })).sort((x, y) => x.s - y.s);
  return (
    <div className="lp-learn-pref">
      <p className="lp-learn-ask">Which would you cut first?</p>
      <div className="lp-learn-duel">
        <button type="button" onClick={() => cut(a, b)}>
          {ITEMS[a]}
        </button>
        <span aria-hidden="true">or</span>
        <button type="button" onClick={() => cut(b, a)}>
          {ITEMS[b]}
        </button>
      </div>
      <LayoutGroup>
        <ol className="lp-learn-rank" aria-label="Cut first, at the top">
          {order.map((o) => (
            <motion.li key={o.name} layout transition={{ type: "spring", stiffness: 260, damping: 28 }}>
              <span>{o.name}</span>
              <span className="lp-learn-bar" style={{ "--v": (1 / (1 + Math.exp(-o.s))).toFixed(3) } as React.CSSProperties} />
            </motion.li>
          ))}
        </ol>
      </LayoutGroup>
      <p className="lp-learn-note">{duels.length ? `${duels.length} answered. Each one moves the order.` : "Pick one; the list reorders."}</p>
    </div>
  );
}

// ---- habits: the made-up household's average week (accounts.fixture) ----

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const DAY_NAMES = ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"];

function Habits({ still }: { still: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const seen = useInView(ref, { once: true, amount: 0.5 });
  const week = ACCOUNTS.weekday;
  const top = Math.max(...week);
  const peak = [...week.keys()].sort((x, y) => (week[y] ?? 0) - (week[x] ?? 0)).slice(0, 2).sort();
  const money = (m: number) => formatMoney({ minor: m, currency: "GBP" });
  return (
    <div ref={ref} className="lp-learn-week" data-on={still || seen ? "" : undefined}>
      <ol className="lp-learn-days">
        {week.map((v, i) => (
          <li key={DAYS[i]} data-peak={peak.includes(i) ? "" : undefined} style={{ "--v": (v / top).toFixed(3), "--i": i } as React.CSSProperties}>
            <span className="lp-learn-day-bar" />
            <span className="lp-learn-day">{DAYS[i]}</span>
          </li>
        ))}
      </ol>
      <p className="lp-learn-note">
        {DAY_NAMES[peak[0] ?? 0]} and {DAY_NAMES[peak[1] ?? 1]} cost the most: about {money(week[peak[0] ?? 0] ?? 0)} and {money(week[peak[1] ?? 1] ?? 0)} in an average week, from the last 13 weeks of one made-up household.
      </p>
    </div>
  );
}

// ---- a small local model: the reply is written on the Mac ----

const REPLY = `You spent ${formatMoney(ANSWER.total)} on eating out in ${OVERVIEW.month}, across ${ANSWER.count} payments.`;

function Local({ still }: { still: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const seen = useInView(ref, { once: true, amount: 0.6 });
  const words = REPLY.split(" ");
  const [n, setN] = useState(still ? words.length : 0);
  useEffect(() => {
    if (still || !seen) return;
    const id = window.setInterval(() => setN((k) => (k >= words.length ? k : k + 1)), 70);
    return () => window.clearInterval(id);
  }, [still, seen, words.length]);
  return (
    <div ref={ref} className="lp-learn-local">
      <div className="lp-learn-mac">
        <span className="lp-learn-chip" aria-hidden="true" />
        <p className="lp-learn-reply" aria-label={REPLY}>
          <span aria-hidden="true">{words.slice(0, n).join(" ")}</span>
          {n < words.length && <span className="lp-learn-caret" aria-hidden="true" />}
        </p>
      </div>
      <p className="lp-learn-bytes">
        Sent over the internet: <strong>0 bytes</strong>
      </p>
    </div>
  );
}

/** "It learns you": four honest pieces of what adapts, each from the engine's own rules. */
export function Learns() {
  const still = useStill();
  return (
    <section className="lp-learn" aria-labelledby="lp-learn-title">
      <div className="lp-wrap">
        <header className="lp-learn-head">
          <h2 id="lp-learn-title" className="lp-h2">
            It learns you.
          </h2>
          <p className="lp-learn-lead">It learns from what you accept, not from what you click. Opening a card or scrolling past it teaches it nothing.</p>
        </header>
        <div className="lp-learn-grid">
          <Rise still={still} className="lp-slot lp-learn-a">
            <Material className="lp-learn-tile">
              <h3 className="lp-h3">Insights that earn their place</h3>
              <p>Each kind of Insight keeps a count of useful and dismissed. Press either: the curve of how sure it is narrows, and the order changes. A version that also weighs the time of month is being tested quietly, beside the live ranking.</p>
              <Relevance />
            </Material>
          </Rise>
          <Rise still={still} delay={0.08} className="lp-slot lp-learn-b">
            <Material className="lp-learn-tile">
              <h3 className="lp-h3">Your priorities, two at a time</h3>
              <p>One question, two choices. It asks the pair it is least sure about, so a few answers go a long way.</p>
              <Preferences />
            </Material>
          </Rise>
          <Rise still={still} delay={0.16} className="lp-slot lp-learn-c">
            <Material className="lp-learn-tile">
              <h3 className="lp-h3">Your habits</h3>
              <Habits still={still} />
            </Material>
          </Rise>
          <Rise still={still} delay={0.24} className="lp-slot lp-learn-d">
            <Material className="lp-learn-tile">
              <h3 className="lp-h3">A small model, on your Mac</h3>
              <p>Answers are written by a small model running locally. It is never trained on your data: what it knows about you lives in your ledger, where you can read it and delete it.</p>
              <Local still={still} />
            </Material>
          </Rise>
        </div>
      </div>
    </section>
  );
}
