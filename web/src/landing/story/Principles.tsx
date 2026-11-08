import { motion, useScroll, useTransform } from "motion/react";
import { useRef } from "react";
import { PRINCIPLES } from "./story.fixture";

/** #76 focus list: opacity and Manrope's weight axis follow the item's distance from the middle of the screen. */
function Focused({ text }: { text: string }) {
  const ref = useRef<HTMLLIElement>(null);
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start end", "end start"] });
  const opacity = useTransform(scrollYProgress, [0.1, 0.5, 0.9], [0.42, 1, 0.42]);
  const fontWeight = useTransform(scrollYProgress, [0.1, 0.5, 0.9], [380, 900, 380]);
  return (
    <motion.li ref={ref} className="st-principle" style={{ opacity, fontWeight }}>
      {text}
    </motion.li>
  );
}

export function Principles({ still }: { still: boolean }) {
  return (
    <section className="st-principles" aria-labelledby="st-principles-title">
      <div className="lp-wrap st-principles-grid">
        <h2 id="st-principles-title" className="lp-h2">
          Five rules it keeps.
        </h2>
        <ul className="st-principle-list">
          {PRINCIPLES.map((p) =>
            still ? (
              <li key={p} className="st-principle">
                {p}
              </li>
            ) : (
              <Focused key={p} text={p} />
            ),
          )}
        </ul>
      </div>
    </section>
  );
}
