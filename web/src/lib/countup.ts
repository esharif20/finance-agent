import { animate, useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";

/** Eases an integer from its last shown value to `target`. Snaps under reduced motion. */
export function useCountUp(target: number, enabled = true): number {
  const reduce = useReducedMotion();
  const [shown, setShown] = useState(enabled && !reduce ? 0 : target);
  const from = useRef(shown);

  useEffect(() => {
    if (!enabled || reduce) {
      from.current = target;
      setShown(target);
      return;
    }
    const controls = animate(from.current, target, {
      duration: 0.5,
      ease: [0.2, 0.8, 0.2, 1],
      onUpdate: (v) => {
        from.current = Math.round(v);
        setShown(from.current);
      },
    });
    return () => controls.stop();
  }, [target, enabled, reduce]);

  return shown;
}
