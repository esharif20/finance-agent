import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// jsdom runs no CSS animations, so these guard the stylesheet itself.
const css = readFileSync(resolve(__dirname, "index.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const rule = (selector: string, within = css) => {
  const at = within.indexOf(`${selector} {`);
  expect(at, `rule ${selector}`).toBeGreaterThanOrEqual(0);
  return within.slice(at, within.indexOf("}", at));
};

describe("ambient stylesheet", () => {
  it("animates only transform or opacity in every keyframe", () => {
    const frames = [...css.matchAll(/@keyframes\s+[\w-]+\s*\{([\s\S]*?)\}\s*\}/g)];
    expect(frames.length).toBeGreaterThan(0);
    for (const [, body] of frames) {
      const props = [...body!.matchAll(/([a-z-]+)\s*:/g)].map((m) => m[1]);
      expect(props.filter((p) => p !== "transform" && p !== "opacity")).toEqual([]);
    }
  });

  it("loops the drift with longhands, since the minifier turns a nameless `animation` shorthand into `animation:none`", () => {
    const fields = rule(".ambient i");
    expect(fields).not.toMatch(/\banimation\s*:/);
    expect(fields).toMatch(/animation-iteration-count:\s*infinite/);
    expect(fields).toMatch(/animation-direction:\s*alternate/);
    expect(fields).toMatch(/animation-duration:\s*\d/);
  });

  it("removes the drift entirely under prefers-reduced-motion", () => {
    const at = css.indexOf("@media (prefers-reduced-motion: reduce)");
    expect(at).toBeGreaterThanOrEqual(0);
    expect(rule(".ambient i", css.slice(at))).toMatch(/animation:\s*none\s*!important/);
  });
});
