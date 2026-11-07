import { animate, type MotionValue, motionValue } from "motion/react";
import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import type { Hover, Pose } from "@/lib/cardPoses";

/** Spring for #59's content transition, so the view below settles with the card. */
export const SELECT_SPRING = { type: "spring", duration: 0.5, bounce: 0.18 } as const;
const HOVER_SPRING = { type: "spring", stiffness: 320, damping: 26, mass: 0.7 } as const;

type Springs = Record<Exclude<keyof Pose, "layer">, MotionValue<number>>;
const KEYS = ["x", "y", "z", "rx", "ry", "rz", "s"] as const;
export type Light = { x: number; y: number; on: number };

/** One set of springs per card, plus the pointer light, moved towards each render's `poses`
    (instantly under reduced motion). Whenever one moves, `draw` gets the current poses, at
    most once a frame. Returns `redraw(withScene = true)` to draw now. */
export function useCardSprings(
  cards: readonly unknown[],
  poses: Pose[],
  o: {
    frontIndex: number;
    rising: number | null;
    reduce: boolean;
    hover: Hover | null;
    width: number;
    heightAt: (i: number) => number;
  },
  draw: (now: Pose[], light: Light, withScene: boolean) => void,
) {
  const springs = useMemo<Springs[]>(
    () =>
      cards.map(() => Object.fromEntries(KEYS.map((k) => [k, motionValue(k === "s" ? 1 : 0)])) as Springs),
    [cards],
  );
  const light = useMemo(() => ({ x: motionValue(0), y: motionValue(0), on: motionValue(0) }), []);

  const frame = useRef((_withScene = true) => {});
  frame.current = (withScene = true) => {
    const now = springs.map((s, i) => ({
      ...(Object.fromEntries(KEYS.map((k) => [k, s[k].get()])) as Omit<Pose, "layer">),
      layer: poses[i]?.layer ?? 0,
    }));
    draw(now, { x: light.x.get(), y: light.y.get(), on: light.on.get() }, withScene);
  };
  const redraw = useRef((withScene = true) => frame.current(withScene)).current;

  useEffect(() => {
    let queued = 0;
    const schedule = () => {
      if (!queued) queued = requestAnimationFrame(() => ((queued = 0), frame.current()));
    };
    const offs = [
      ...springs.flatMap((s) => KEYS.map((k) => s[k].on("change", schedule))),
      light.x.on("change", schedule),
      light.y.on("change", schedule),
      light.on.on("change", schedule),
    ];
    document.addEventListener("visibilitychange", schedule);
    return () => {
      for (const off of offs) off();
      document.removeEventListener("visibilitychange", schedule);
      cancelAnimationFrame(queued);
    };
  }, [springs, light]);

  const placed = useRef<Springs[] | null>(null);
  useLayoutEffect(() => {
    const first = placed.current !== springs; // new cards start where they belong
    placed.current = springs;
    poses.forEach((p, i) => {
      const s = springs[i];
      if (!s) return;
      const chosen = i === o.frontIndex || i === o.rising;
      for (const k of KEYS) {
        if (first || o.reduce) s[k].jump(p[k]);
        else animate(s[k], p[k], chosen && k !== "rx" && k !== "ry" ? SELECT_SPRING : HOVER_SPRING);
      }
    });
    const lit = o.hover && !o.reduce ? o.hover : null;
    const target = lit ? poses[lit.index] : undefined;
    animate(light.on, lit ? 1 : 0, { duration: 0.2 });
    if (lit && target) {
      animate(light.x, lit.px * o.width + target.x, HOVER_SPRING);
      animate(light.y, lit.py * o.heightAt(lit.index) + target.y, HOVER_SPRING);
    }
    frame.current(false); // DOM only: undo React's write of the target transform; the frame renders the scene
  });

  return redraw;
}
