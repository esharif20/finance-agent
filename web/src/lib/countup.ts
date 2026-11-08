import { animate, useReducedMotion } from "motion/react";
import { type RefObject, useEffect, useRef, useState } from "react";

/** Eases an integer from its last shown value to `target`. Snaps under reduced motion.
 * With `ref`, the first count waits until that element is first in view. */
export function useCountUp(target: number, enabled = true, ref?: RefObject<Element | null>): number {
  const reduce = useReducedMotion();
  const [shown, setShown] = useState(enabled && !reduce ? 0 : target);
  const [seen, setSeen] = useState(() => !ref || typeof IntersectionObserver !== "function");
  const from = useRef(shown);

  useEffect(() => {
    if (seen) return;
    const el = ref?.current;
    if (!el) return setSeen(true);
    const io = new IntersectionObserver(([e]) => {
      if (e?.isIntersecting) {
        setSeen(true);
        io.disconnect();
      }
    });
    io.observe(el);
    return () => io.disconnect();
  }, [seen, ref]);

  useEffect(() => {
    if (!enabled || reduce) {
      from.current = target;
      setShown(target);
      return;
    }
    if (!seen) return;
    const controls = animate(from.current, target, {
      duration: 0.7,
      ease: [0.23, 1, 0.32, 1],
      onUpdate: (v) => {
        from.current = Math.round(v);
        setShown(from.current);
      },
    });
    return () => controls.stop();
  }, [target, enabled, reduce, seen]);

  return shown;
}
