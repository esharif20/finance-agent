import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useRef } from "react";
import { Field, useStill } from "./fx";

// What makes it an agent; every line is something the app does today
const VERBS = [
  { name: "Watches", tip: "Syncs your accounts in the background and spots a new subscription from its first charge." },
  { name: "Plans", tip: "Works out which lookups a question needs before any model is asked." },
  { name: "Checks", tip: "Every figure in a reply must match your ledger, or it isn’t shown." },
  { name: "Remembers", tip: "Keeps what you tell it to remember, where you can read and delete it." },
  { name: "Learns", tip: "Ranks Insights by what you mark useful, not by what you click." },
  { name: "Suggests", tip: "Proposes a change and waits for you to press Confirm. It never moves money." },
];

/** What it does on its own: six verbs, each lit as it reaches the middle of the screen and dimmed
    as it leaves, scrubbed by ScrollTrigger (after React Bits' ScrollReveal, MIT,
    github.com/DavidHDev/react-bits). The heading holds on the left while the verbs pass. Under
    reduced motion every verb is lit and nothing moves. */
export function Verbs() {
  const still = useStill();
  const ref = useRef<HTMLElement>(null);
  useGSAP(
    () => {
      if (still) return;
      gsap.registerPlugin(ScrollTrigger); // here, not at import: it reads matchMedia on register
      for (const row of gsap.utils.toArray<HTMLElement>(".lp-verb", ref.current)) {
        gsap
          .timeline({ scrollTrigger: { trigger: row, start: "top 85%", end: "bottom 15%", scrub: 0.6 } })
          .fromTo(row, { opacity: 0.18, x: -24 }, { opacity: 1, x: 0, ease: "power2.out", duration: 0.4 })
          .to(row, { opacity: 1, duration: 0.2 })
          .to(row, { opacity: 0.18, ease: "power2.in", duration: 0.4 });
      }
    },
    { scope: ref, dependencies: [still] },
  );
  return (
    <section ref={ref} id="does" className="lp-verbs" aria-labelledby="lp-verbs-title">
      <Field tone="paper" />
      <div className="lp-wrap lp-verbs-grid">
        <div className="lp-verbs-head">
          <h2 id="lp-verbs-title" className="lp-h2">
            What it does on its own
          </h2>
          <p>An agent, not a chatbot: it works through your accounts between questions, and asks before it changes anything.</p>
        </div>
        <ol className="lp-verbs-list">
          {VERBS.map((v) => (
            <li key={v.name} className="lp-verb">
              <span className="lp-verb-name">{v.name}</span>
              <span className="lp-verb-tip">{v.tip}</span>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
