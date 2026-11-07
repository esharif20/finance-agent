// @vitest-environment node
import { resolve } from "node:path";
import { build, type Rollup } from "vite";
import { beforeAll, describe, expect, it } from "vitest";

// Frosted cards are judged on what ships: the production minifier has dropped and rewritten
// rules before (see ambient-css.test.ts), so this builds the app and reads the emitted CSS.
let css = "";
beforeAll(async () => {
  const out = (await build({
    root: resolve(__dirname, ".."),
    logLevel: "silent",
    build: { write: false },
  })) as Rollup.RollupOutput;
  css = out.output
    .filter((f): f is Rollup.OutputAsset => f.type === "asset" && f.fileName.endsWith(".css"))
    .map((f) => String(f.source))
    .join("\n");
}, 60_000);

const block = (at: number) => {
  let depth = 0;
  for (let i = css.indexOf("{", at); i < css.length; i++) {
    if (css[i] === "{") depth++;
    else if (css[i] === "}" && --depth === 0) return css.slice(at, i + 1);
  }
  return css.slice(at);
};
const atRule = (re: RegExp) => {
  const m = re.exec(css);
  expect(m, String(re)).not.toBeNull();
  return block(m!.index);
};

describe("frosted cards in the production build", () => {
  it("defines a translucent card surface and hairline on :root, with dark values", () => {
    // The minifier rewrites rgb(r g b / a) as #rrggbbaa or #rgba: either way it must carry alpha.
    const translucent = /--card-glass:\s*(#[0-9a-f]{8}|#[0-9a-f]{4}|rgba?\([^)]*[/,]\s*0?\.\d)[;}]/gi;
    expect(css.match(translucent)?.length, "light and dark --card-glass").toBeGreaterThanOrEqual(2);
    const darks = [...css.matchAll(/@media\s*\(prefers-color-scheme:\s*dark\)/g)].map((m) => block(m.index));
    expect(darks.some((d) => new RegExp(translucent.source, "i").test(d) && /--card-edge:/.test(d))).toBe(true);
  });

  it("blurs what is behind a card", () => {
    expect(css).toMatch(/\.card-glass\{[^}]*backdrop-filter:blur\(/);
    expect(css).toMatch(/\.card-glass\{[^}]*background:var\(--card-glass\)/);
  });

  it("falls back to the opaque card where backdrop-filter is unsupported", () => {
    const rule = atRule(/@supports\s+not\s*\(\(?(-webkit-)?backdrop-filter/);
    expect(rule).toMatch(/\.card-glass\{[^}]*background:var\(--card\)/);
  });

  it("falls back to the opaque card under prefers-reduced-transparency", () => {
    const rule = atRule(/@media\s*\(prefers-reduced-transparency:\s*reduce\)/);
    expect(rule).toMatch(/\.card-glass\{[^}]*background:var\(--card\)/);
    expect(rule).toMatch(/\.card-glass\{[^}]*backdrop-filter:none/);
  });
});
