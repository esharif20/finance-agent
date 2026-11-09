// @vitest-environment jsdom
import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { routeTable, viewFor } from "@/main";
import { ACCOUNTS } from "./accounts.fixture";
import { HEADLINE, HEADLINE_LINES, Hero } from "./Hero";

const web = path.resolve(import.meta.dirname, "../..");

const mediaStub = (reduce: boolean) => (q: string) => ({
  matches: reduce && q.includes("prefers-reduced-motion"),
  media: q,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
});

afterEach(() => vi.unstubAllGlobals());

class NoObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}

describe("route split", () => {
  it("the local build has the app alone at / and no landing route", () => {
    expect(routeTable(false)).toEqual({ "/": "app" });
    expect(Object.values(routeTable(false))).not.toContain("landing");
    for (const p of ["/", "/app", "/anything"]) expect(viewFor(p, false)).toBe("app");
  });

  it("the demo build serves the landing page at / and the app under /app", () => {
    expect(viewFor("/", true)).toBe("landing");
    expect(viewFor("/app", true)).toBe("app");
    expect(viewFor("/app/", true)).toBe("app");
    expect(viewFor("/apples", true)).toBe("landing");
  });
});

describe("hero headline", () => {
  it("draws each line as letters, with the full text for screen readers", () => {
    vi.stubGlobal("matchMedia", mediaStub(false));
    vi.stubGlobal("IntersectionObserver", NoObserver);
    render(<Hero />);
    const h1 = screen.getByRole("heading", { level: 1, name: HEADLINE });
    expect(h1.querySelectorAll(".lp-flex-line")).toHaveLength(HEADLINE_LINES.length);
    expect(h1.querySelectorAll("[data-l]").length).toBe(HEADLINE.replace(/ /g, "").length);
  });

  it("renders static under prefers-reduced-motion", () => {
    vi.stubGlobal("matchMedia", mediaStub(true));
    const { container } = render(<Hero />);
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1.textContent).toBe(HEADLINE);
    expect(container.querySelector(".lp-stage")).toBeNull();
    // the still composition: every account and the combined total
    for (const a of ACCOUNTS.accounts) expect(screen.getAllByText(a.name).length).toBeGreaterThan(0);
    expect(container.querySelector(".lp-combined")).toBeTruthy();
    // nothing is held mid-move: no inline transform, opacity or clip on the hero
    for (const el of container.querySelectorAll<HTMLElement>("*")) {
      expect(el.style.transform || "none").toBe("none");
      expect(el.style.clipPath).toBe("");
    }
  });
});

describe("hero on phones", () => {
  // a phone: coarse pointer, no reduced motion
  const phone = (q: string) => ({ ...mediaStub(false)(q), matches: q.includes("pointer: coarse") });

  it("draws the cards as live DOM: no canvas, no video, no raster frames", () => {
    vi.stubGlobal("matchMedia", phone);
    vi.stubGlobal("IntersectionObserver", NoObserver);
    const { container } = render(<Hero />);
    expect(container.querySelector("canvas")).toBeNull();
    expect(container.querySelector("video")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelectorAll(".lp-acard-slot .lp-acard")).toHaveLength(ACCOUNTS.accounts.length);
    // every balance is live text
    for (const a of ACCOUNTS.accounts) expect(screen.getAllByText(a.name).length).toBeGreaterThan(0);
    // no light layers or hover peek on touch: a few transformed divs only
    expect(container.querySelector(".lp-acard-light, .lp-acard-back, .lp-acard-floor, .lp-peek")).toBeNull();
    // each card wears its own made-up row, inside its slot, so the row moves with the card
    expect(container.querySelectorAll(".lp-acard-slot > .lp-worn")).toHaveLength(ACCOUNTS.accounts.length);
    // the old scattered planes are gone: no trace tile, no board, no ghost figures
    expect(container.querySelector(".lp-trace, .lp-hero-board, .lp-hero-ghost")).toBeNull();
  });
});

// A fixed list of UK banks and building societies. None may appear in the public demo bundle.
const BANKS = [
  "barclays", "hsbc", "lloyds", "natwest", "santander", "halifax", "nationwide", "monzo",
  "starling", "revolut", "tsb", "metro bank", "first direct", "co-operative bank", "virgin money",
  "royal bank of scotland", "rbs", "bank of scotland", "tesco bank", "coutts", "chase", "ulster bank",
  "danske", "clydesdale", "yorkshire building society", "kroo", "chase uk", "aib", "atom bank",
];

const files = (dir: string): string[] =>
  readdirSync(dir).flatMap((f) => {
    const p = path.join(dir, f);
    return statSync(p).isDirectory() ? files(p) : [p];
  });

describe("demo bundle", () => {
  it("has no card-art URL and no real bank name", { timeout: 180_000 }, () => {
    execFileSync("pnpm", ["exec", "vite", "build", "--outDir", "dist-demo", "--emptyOutDir", "--logLevel", "error"], {
      cwd: web,
      env: { ...process.env, VITE_DEMO: "1" },
      stdio: "pipe",
    });
    const out = path.join(web, "dist-demo");
    const all = files(out);
    expect(all.some((f) => f.endsWith(".js"))).toBe(true);
    const hits: string[] = [];
    for (const f of all) {
      if (!/\.(js|css|html|svg|json|txt)$/.test(f)) continue;
      const text = readFileSync(f, "utf8").toLowerCase();
      if (text.includes("card-art")) hits.push(`${path.basename(f)}: card-art`);
      for (const b of BANKS) if (new RegExp(`\\b${b}\\b`).test(text)) hits.push(`${path.basename(f)}: ${b}`);
    }
    expect(hits).toEqual([]);
    // the landing page is in it
    expect(all.some((f) => readFileSync(f, "utf8").includes(HEADLINE))).toBe(true);
  });

  // CI builds the local bundle before it runs the tests
  it.skipIf(!existsSync(path.join(web, "dist")))("the local bundle carries no landing code", () => {
    const local = files(path.join(web, "dist")).filter((f) => f.endsWith(".js"));
    for (const f of local) expect(readFileSync(f, "utf8")).not.toContain(HEADLINE);
  });
});
