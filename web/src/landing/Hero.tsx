import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { type CSSProperties, useRef } from "react";
import { formatMoney } from "@/lib/money";
import { Corners, Field, Magnetic, Roll, useStill } from "./fx";
import { ANSWER, OVERVIEW } from "./story/story.fixture";
import "./hero.css";

export const HEADLINE = "An AI agent for your money, on your own Mac.";
export const HEADLINE_LINES = ["An AI agent", "for your money,", "on your own Mac."];
const LINE =
  "It watches every account, spots what changed, answers with figures it has checked, and learns what you care about. It suggests; you decide. Nothing leaves your Mac unless you say so.";

// What makes it an agent; every line is something the app does today
const CAPS = [
  { name: "Watches", tip: "Syncs in the background and spots a new subscription from its first charge." },
  { name: "Plans", tip: "Works out which lookups a question needs before any model is asked." },
  { name: "Checks", tip: "Every figure in a reply must match your ledger, or it isn’t shown." },
  { name: "Remembers", tip: "Keeps what you tell it to remember, where you can read and delete it." },
  { name: "Learns", tip: "Ranks Insights by what you mark useful, not by what you click." },
  { name: "Suggests", tip: "Proposes a change and waits for you to press Confirm. It never moves money." },
];

// the agent's run on the made-up ledger, step by step; the figure is the fixture's, never computed here
const STEPS = ["Planned one lookup: spending by category", `Ran it on the ledger: ${ANSWER.count} payments`, "Checked the figure against them"];
const FIGURE = formatMoney(ANSWER.total);

/** The hero, a poster: the claim set huge across the left, and on the right a mint block where the
    agent answers a question live. Motion is one GSAP timeline on load (letters rise per line, after
    React Bits' SplitText; the question types in, after its TextType; the figure counts up, after its
    CountUp; all MIT, github.com/DavidHDev/react-bits) and one ScrollTrigger scrub on the way out,
    stepped by the same ticker as Lenis. The headline never reacts to the pointer. Under reduced
    motion nothing is set and the finished poster shows. */
export function Hero({ delay = 0, play = true }: { delay?: number; play?: boolean }) {
  const still = useStill();
  const root = useRef<HTMLElement>(null);
  const typed = useRef<HTMLSpanElement>(null);
  const fig = useRef<HTMLSpanElement>(null);

  // the start frame, set before paint so nothing flashes in its final place first
  useGSAP(
    () => {
      if (still) return;
      gsap.registerPlugin(ScrollTrigger); // here, not at import: it reads matchMedia on register
      const mm = gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        gsap.set(".hp-ch", { yPercent: 115 });
        gsap.set(".hp-agent", { yPercent: 18, opacity: 0 });
        gsap.set(".hp-step, .hp-answer, .hp-foot > *, .hp-cap", { y: 18, opacity: 0 });
        if (typed.current) typed.current.textContent = "";
        if (fig.current) fig.current.textContent = formatMoney({ minor: 0, currency: ANSWER.total.currency });
        // leaving: the lines slide apart like a poster being pulled, the block lifts, the whole sheet sinks under the next chapter
        const wide = window.matchMedia("(min-width: 900px)").matches;
        const out = gsap.timeline({ scrollTrigger: { trigger: root.current, start: "top top", end: "bottom top", scrub: 0.3 } });
        out.to(".hp-in", { yPercent: 22, scale: 0.94, opacity: 0.3, ease: "none" }, 0);
        if (wide) {
          gsap.utils.toArray<HTMLElement>(".hp-line", root.current).forEach((l, i) => out.to(l, { xPercent: i % 2 ? 7 : -5, ease: "none" }, 0));
          out.to(".hp-agent-in", { yPercent: -14, ease: "none" }, 0);
        }
      });
      return () => mm.revert();
    },
    { scope: root },
  );

  // the entrance, once the tally hands over
  useGSAP(
    () => {
      if (!play || still) return;
      const q = { n: 0 };
      const f = { v: 0 };
      const tl = gsap.timeline({ delay });
      gsap.utils.toArray<HTMLElement>(".hp-line", root.current).forEach((l, i) => {
        tl.to(l.querySelectorAll(".hp-ch"), { yPercent: 0, duration: 0.9, ease: "expo.out", stagger: { each: 0.022, ease: "power1.in" } }, i * 0.11);
      });
      tl.to(".hp-agent", { yPercent: 0, opacity: 1, duration: 0.9, ease: "expo.out" }, 0.25)
        .to(q, {
          n: ANSWER.question.length,
          duration: ANSWER.question.length * 0.022,
          ease: "none",
          onUpdate: () => void (typed.current && (typed.current.textContent = ANSWER.question.slice(0, Math.round(q.n)))),
        }, 0.7)
        .to(".hp-step", { y: 0, opacity: 1, duration: 0.5, ease: "power3.out", stagger: 0.28 }, ">0.1")
        .to(".hp-answer", { y: 0, opacity: 1, duration: 0.5, ease: "power3.out" }, ">-0.1")
        .to(f, {
          v: ANSWER.total.minor,
          duration: 1.1,
          ease: "expo.out",
          onUpdate: () => void (fig.current && (fig.current.textContent = formatMoney({ minor: Math.round(f.v), currency: ANSWER.total.currency }))),
          onComplete: () => void (fig.current && (fig.current.textContent = FIGURE)),
        }, "<")
        .to(".hp-foot > *, .hp-cap", { y: 0, opacity: 1, duration: 0.7, ease: "power3.out", stagger: 0.05 }, 0.45);
    },
    { scope: root, dependencies: [play, still] },
  );

  return (
    <section ref={root} className="hp" data-still={still ? "" : undefined} aria-labelledby="lp-title">
      <Field tone="night" />
      <div className="hp-in">
        <Corners tl="Local first" tr="Runs on macOS" bl="Open source" br="The demo uses made-up data" />
        <h1 id="lp-title" className="hp-title" aria-label={HEADLINE}>
          {HEADLINE_LINES.map((line, i) => (
            <span key={line} className="hp-line" aria-hidden="true">
              {line.split(" ").map((w, k) => (
                <span key={k} className="hp-word">
                  {k > 0 && " "}
                  {[...w].map((c, j) => (
                    <span key={j} className="hp-ch" data-l="">
                      {c}
                    </span>
                  ))}
                </span>
              ))}
              {i < HEADLINE_LINES.length - 1 && " "}
            </span>
          ))}
        </h1>
        <div className="hp-agent">
          <div className="hp-agent-in" style={{ "--d": `${delay}s` } as CSSProperties}>
            <Corners tl="The agent, live" tr="Local model" />
            <p className="hp-q">
              <span className="hp-q-ghost" aria-hidden="true">
                {ANSWER.question}
              </span>
              <span ref={typed} className="hp-q-typed">
                {ANSWER.question}
              </span>
            </p>
            <ol className="hp-steps">
              {STEPS.map((s) => (
                <li key={s} className="hp-step">
                  {s}
                </li>
              ))}
            </ol>
            <p className="hp-answer">
              <span ref={fig} className="hp-fig">
                {FIGURE}
              </span>
              <span className="hp-fig-note">
                on eating out in {OVERVIEW.month}, across {ANSWER.count} payments. No AI call made the figure.
              </span>
            </p>
          </div>
        </div>
        <div className="hp-foot">
          <p className="hp-lede">{LINE}</p>
          <div className="lp-actions">
            <Magnetic href="/app" className="lp-pill lp-pill-primary">
              <Roll text="Open the demo" />
            </Magnetic>
            <a className="lp-link" href="#what">
              <Roll text="See what it does" />
            </a>
          </div>
        </div>
        <ul className="hp-caps" aria-label="What it does on its own">
          {CAPS.map((c) => (
            <li key={c.name} className="hp-cap">
              <b>{c.name}</b>
              <span>{c.tip}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
