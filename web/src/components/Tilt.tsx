import { motion, useReducedMotion, useSpring } from "motion/react";
import type * as React from "react";

const MAX = 2.5; // degrees
const SPRING = { stiffness: 170, damping: 22, mass: 0.6 };

/** A hero card that tilts a little toward a mouse pointer. Touch, pens and reduced motion stay flat. */
export function Tilt({ children, className }: { children: React.ReactNode; className?: string }) {
  const reduce = useReducedMotion();
  const rotateX = useSpring(0, SPRING);
  const rotateY = useSpring(0, SPRING);
  const reset = () => {
    rotateX.set(0);
    rotateY.set(0);
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (reduce || e.pointerType !== "mouse") return;
    const r = e.currentTarget.getBoundingClientRect();
    rotateY.set(((e.clientX - r.left) / r.width - 0.5) * 2 * MAX);
    rotateX.set(-((e.clientY - r.top) / r.height - 0.5) * 2 * MAX);
  };
  return (
    <motion.div
      className={className}
      style={{ rotateX, rotateY, transformPerspective: 1100 }}
      onPointerMove={onPointerMove}
      onPointerLeave={reset}
    >
      {children}
    </motion.div>
  );
}
