import { describe, expect, it } from "vitest";
import { chartGeometry } from "./forecast";

// synthetic
const months = [
  { month: "2026-04", point: 100000, lower: 90000, upper: 110000 },
  { month: "2026-05", point: 80000, lower: 60000, upper: 100000 },
  { month: "2026-06", point: 70000, lower: 40000, upper: 100000 },
];

describe("chartGeometry", () => {
  it("puts higher money higher up, and the band around the line", () => {
    const g = chartGeometry(months, 300, 100);
    expect(g.y(110000)).toBeLessThan(g.y(100000));
    expect(g.points.map((p) => p.x)).toEqual([0, 150, 300]);
    for (const p of g.points) {
      expect(p.yUpper).toBeLessThanOrEqual(p.y);
      expect(p.yLower).toBeGreaterThanOrEqual(p.y);
    }
  });
  it("keeps the whole band inside the box", () => {
    const g = chartGeometry(months, 300, 100);
    for (const p of g.points) {
      expect(p.yLower).toBeLessThanOrEqual(100);
      expect(p.yUpper).toBeGreaterThanOrEqual(0);
    }
  });
  it("fits the band, so it fills most of the box instead of a strip at the top", () => {
    const g = chartGeometry(months, 300, 100);
    const top = Math.min(...g.points.map((p) => p.yUpper));
    const bottom = Math.max(...g.points.map((p) => p.yLower));
    expect(bottom - top).toBeGreaterThan(55);
    expect(g.min).toBeGreaterThan(0);
  });
  it("reaches below zero when a month does, so the dip stays visible", () => {
    const g = chartGeometry([{ month: "2026-04", point: -5000, lower: -20000, upper: 10000 }, ...months], 300, 100);
    expect(g.min).toBeLessThanOrEqual(-20000);
    expect(g.y(0)).toBeGreaterThan(0);
    expect(g.y(0)).toBeLessThan(100);
  });
  it("draws a band polygon and a line path with one vertex per month", () => {
    const g = chartGeometry(months, 300, 100);
    expect(g.line.split("L").length).toBe(3);
    expect(g.band.match(/[ML]/g)?.length).toBe(6);
  });
  it("copes with one month and with none", () => {
    expect(chartGeometry([months[0] as (typeof months)[number]], 300, 100).points).toHaveLength(1);
    expect(chartGeometry([], 300, 100).line).toBe("");
  });
});
