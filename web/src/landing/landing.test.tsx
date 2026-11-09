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

describe("hero film on phones", () => {
  // a phone: coarse pointer, no reduced motion
  const phone = (q: string) => ({ ...mediaStub(false)(q), matches: q.includes("pointer: coarse") });
  const ctx2d = { clearRect: vi.fn(), drawImage: vi.fn() };
  const withCanvas = () =>
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(((t: string) => (t === "2d" ? ctx2d : null)) as never);
  // jsdom has no img.decode(): give each test its own
  const decodes = (impl: () => Promise<void>) =>
    Object.defineProperty(HTMLImageElement.prototype, "decode", { configurable: true, writable: true, value: impl });
  afterEach(() => {
    vi.restoreAllMocks();
    delete (HTMLImageElement.prototype as { decode?: unknown }).decode;
  });

  it("paints the poster under the frames from the first render, and no <video>", () => {
    vi.stubGlobal("matchMedia", phone);
    vi.stubGlobal("IntersectionObserver", NoObserver);
    withCanvas();
    decodes(() => new Promise(() => {}));
    const { container } = render(<Hero />);
    expect(container.querySelector(".lp-hero")?.getAttribute("data-film")).toBe("wait");
    expect(container.querySelector(".lp-film-poster")?.getAttribute("src")).toBe("/hero/cards.webp");
    expect(container.querySelector("canvas.lp-film-frames")).toBeTruthy();
    expect(container.querySelector("video")).toBeNull();
  });

  it("keeps the poster and releases the pin when a frame fails to load", async () => {
    vi.stubGlobal("matchMedia", phone);
    vi.stubGlobal("IntersectionObserver", NoObserver);
    withCanvas();
    decodes(() => Promise.reject(new Error("404")));
    const { container } = render(<Hero />);
    const { waitFor } = await import("@testing-library/react");
    await waitFor(() => expect(container.querySelector(".lp-hero")?.getAttribute("data-film")).toBe("off"));
    expect(container.querySelector(".lp-film-poster")).toBeTruthy();
  });

  it("falls back to the poster when the frames are not decoded in 1.5 s", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("matchMedia", phone);
    vi.stubGlobal("IntersectionObserver", NoObserver);
    withCanvas();
    decodes(() => new Promise(() => {}));
    const { act } = await import("@testing-library/react");
    const { container } = render(<Hero />);
    act(() => void vi.advanceTimersByTime(1600));
    vi.useRealTimers();
    expect(container.querySelector(".lp-hero")?.getAttribute("data-film")).toBe("off");
  });

  it("scrubs once every frame has decoded", async () => {
    vi.stubGlobal("matchMedia", phone);
    vi.stubGlobal("IntersectionObserver", NoObserver);
    withCanvas();
    decodes(() => Promise.resolve());
    const { container } = render(<Hero />);
    const { waitFor } = await import("@testing-library/react");
    await waitFor(() => expect(container.querySelector(".lp-hero")?.getAttribute("data-film")).toBe("ok"));
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
