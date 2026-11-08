import { motion, useScroll, useTransform } from "motion/react";
import { useRef } from "react";
import { Field, Lines, Material, useStill } from "./fx";

const PROMISES = [
  {
    mark: "Mac",
    title: "Your data stays on your Mac.",
    body: "Statements, transactions and everything the Assistant learns about you sit in a database on your own computer. There is no account to create and no server of ours holding a copy.",
  },
  {
    mark: "Opt-in",
    title: "The cloud is off until you turn it on.",
    body: "The Assistant runs on a local model. If you turn the cloud model on for a conversation, your amounts, shop names and account details are swapped for plain labels such as “Amount 1” and “Merchant A” before anything leaves, and your Mac puts the real values back into the answer. You can read exactly what was sent.",
  },
  {
    mark: "Read only",
    title: "It never moves money.",
    body: "Finance Agent reads your accounts and nothing else. There is no way for it, or for anyone using it, to make a payment or a transfer.",
  },
];

/** A tile that grows up from its baseline like a bar on a chart, scrubbed by scroll. */
function Bar({ i, progress }: { i: number; progress: ReturnType<typeof useScroll>["scrollYProgress"] }) {
  const start = i * 0.12;
  const clip = useTransform(progress, [start, start + 0.55], ["inset(30% 0 0 0)", "inset(0% 0 0 0)"]);
  const lift = useTransform(progress, [start, start + 0.55], ["20%", "0%"]);
  return (
    <motion.div className="lp-bar" style={{ clipPath: clip }}>
      <motion.span className="lp-bar-mark" style={{ y: lift }} aria-hidden="true">
        {PROMISES[i]?.mark}
      </motion.span>
    </motion.div>
  );
}

export function Promises() {
  const still = useStill();
  const ref = useRef<HTMLElement>(null);
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start end", "center center"] });
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
                {still ? (
                  <div className="lp-bar">
                    <span className="lp-bar-mark" aria-hidden="true">
                      {p.mark}
                    </span>
                  </div>
                ) : (
                  <Bar i={i} progress={scrollYProgress} />
                )}
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
