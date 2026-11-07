import { renderHook } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { Pose } from "@/lib/cardPoses";
import { useCardSprings } from "./useCardSprings";

// #129: the spring hook holds one set of springs per card. New cards, and every card under
// reduced motion, start exactly at their pose; `redraw` hands the current poses to `draw`.
const pose = (x: number, layer: number): Pose => ({ x, y: 2 * x, z: 0, rx: 0, ry: 0, rz: 0, s: 1, layer });
const opts = { frontIndex: 0, rising: null, hover: null, width: 360, heightAt: () => 200 };

function mount(reduce: boolean) {
  const draw = vi.fn();
  const cards = [{}, {}];
  const view = renderHook(({ poses }) => useCardSprings(cards, poses, { ...opts, reduce }, draw), {
    initialProps: { poses: [pose(0, 1), pose(10, 0)] },
  });
  return { ...view, draw };
}

it("places new cards at their poses and hands them to draw", () => {
  const { result, draw } = mount(false);
  draw.mockClear();
  result.current();
  const [now, light, withScene] = draw.mock.calls[0] ?? [];
  expect(now).toEqual([pose(0, 1), pose(10, 0)]);
  expect(light).toEqual({ x: 0, y: 0, on: 0 });
  expect(withScene).toBe(true);
});

it("under reduced motion, jumps straight to a new pose with no spring in between", () => {
  const { result, rerender, draw } = mount(true);
  rerender({ poses: [pose(40, 0), pose(10, 1)] });
  draw.mockClear();
  result.current(false);
  expect(draw.mock.calls[0]?.[0]).toEqual([pose(40, 0), pose(10, 1)]);
  expect(draw.mock.calls[0]?.[2]).toBe(false);
});

it("springs towards a new pose when motion is allowed, so the first frame is still on the way", () => {
  const { result, rerender, draw } = mount(false);
  rerender({ poses: [pose(40, 0), pose(10, 1)] });
  draw.mockClear();
  result.current();
  const x = (draw.mock.calls[0]?.[0] as Pose[])[0]?.x ?? -1;
  expect(x).toBeGreaterThanOrEqual(0);
  expect(x).toBeLessThan(40);
});
