import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useRef } from "react";
import { Corners, Field, Lines, Material, useStill } from "./fx";


const PROMISES = [
  {
    mark: "Mac",
    meta: ["Where it lives", "On your computer"],
    title: "Your data stays on your Mac.",
    body: "Statements, transactions and everything the Assistant learns about you sit in a database on your own computer. There is no account to create and no server of ours holding a copy.",
  },
  {
    mark: "Opt-in",
    meta: ["The cloud", "Off by default"],
    title: "The cloud is off until you turn it on.",
    body: "The Assistant runs on a local model. If you turn the cloud model on for a conversation, your amounts, shop names and account details are swapped for plain labels such as “Amount 1” and “Merchant A” before anything leaves, and your Mac puts the real values back into the answer. You can read exactly what was sent.",
  },
  {
    mark: "Read only",
    meta: ["Payments", "None, ever"],
    title: "It never moves money.",
    body: "Finance Agent reads your accounts and nothing else. There is no way for it, or for anyone using it, to make a payment or a transfer.",
  },
];

/** Tiles that rise from their baseline like bars on a chart, scrubbed by scroll (GSAP ScrollTrigger,
    stepped by the same ticker as Lenis). Each block slides up inside a fixed window, transform
    only; as a bar tops out, its word rises letter by letter, after React Bits' SplitText (MIT,
    github.com/DavidHDev/react-bits). Under reduced motion nothing is set, so the final frame shows. */
export function Promises() {
  const still = useStill();
  const ref = useRef<HTMLElement>(null);
  useGSAP(
    () => {
      gsap.registerPlugin(ScrollTrigger); // here, not at import: it reads matchMedia on register
      const mm = gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        const tl = gsap.timeline({
          scrollTrigger: { trigger: ref.current, start: "top bottom", end: "center center", scrub: 0.4 },
        });
        gsap.utils.toArray<HTMLElement>(".lp-bar-fill", ref.current).forEach((bar, i) => {
          const chars = bar.querySelectorAll(".lp-mark-ch");
          tl.fromTo(bar, { yPercent: 32 }, { yPercent: 0, ease: "power2.out", duration: 0.55 }, i * 0.12);
          tl.fromTo(chars, { yPercent: 105 }, { yPercent: 0, ease: "power3.out", duration: 0.2, stagger: 0.025 }, i * 0.12 + 0.3);
        });
      });
      return () => mm.revert();
    },
    { scope: ref },
  );
  return (
    <section ref={ref} id="privacy" className="lp-promises" aria-labelledby="lp-promises-title">
      <Field tone="night" />
      <div className="lp-wrap lp-promise-grid">
        <h2 id="lp-promises-title" className="lp-h2 lp-promises-title">
          <Lines lines={["Three promises,", "in plain words."]} still={still} />
        </h2>
        <ul className="lp-promise-list">
          {PROMISES.map((p, i) => (
            <li key={p.title} className="lp-promise" data-i={i}>
              <Material className="lp-promise-card">
                <div className="lp-bar">
                  <div className="lp-bar-fill">
                    <Corners tl={p.meta[0]} tr={p.meta[1]} />
                    <span className="lp-bar-mark" aria-hidden="true">
                      {[...p.mark].map((c, k) => (
                        <span key={k} className="lp-mark-ch">
                          {c === " " ? " " : c}
                        </span>
                      ))}
                    </span>
                  </div>
                </div>
                <h3 className="lp-h3">{p.title}</h3>
                <p>{p.body}</p>
              </Material>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
