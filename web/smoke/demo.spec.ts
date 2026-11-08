// Demo smoke (#299): build the public demo (scripts/build_demo.sh) into a temp FA_DEMO_DIR, serve
// it with uvicorn, and walk /app in WebKit:
//   - the synthetic-data banner is shown;
//   - the Overview totals equal GET /api/summary for this visitor;
//   - Reset brings back the Persona's first figures after a change;
//   - nothing asks for /api/card-art.
//   pnpm --dir web run smoke:demo [screens-dir]
// Node runs this file as is (type stripping). Playwright comes from the global npm root, as in
// e2e/smoke.mjs; PLAYWRIGHT_MODULE overrides it.
import { execFileSync, spawn } from "node:child_process";
import { createHmac, randomBytes } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";

const require = createRequire(import.meta.url);
const cleanEnv = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.toLowerCase().startsWith("npm_")));
const globalRoot = execFileSync("npm", ["root", "-g"], { env: cleanEnv }).toString().trim();
const { webkit } = require(process.env.PLAYWRIGHT_MODULE ?? `${globalRoot}/playwright`);

const dir = mkdtempSync(join(tmpdir(), "fa-demo-smoke-"));
const port = process.env.FA_DEMO_PORT ?? "8792";
const base = `http://localhost:${port}`;
// a throwaway signing key for this run only, never a literal
const env = { ...cleanEnv, FA_DEMO: "1", FA_DEMO_DIR: dir, FA_DEMO_SECRET: randomBytes(24).toString("hex") };
const shots = process.argv[2];
if (shots) mkdirSync(shots, { recursive: true });

const gbp = (minor: number, sign = false) => {
  const abs = Math.abs(minor);
  const s = `£${Math.floor(abs / 100).toLocaleString("en-GB")}.${String(abs % 100).padStart(2, "0")}`;
  return minor < 0 ? `−${s}` : sign && minor > 0 ? `+${s}` : s;
};

execFileSync("sh", ["scripts/build_demo.sh"], { cwd: "..", env, stdio: ["ignore", "ignore", "inherit"] });
const server = spawn("uv", ["run", "uvicorn", "finance_agent.demo.app:app", "--port", port], {
  cwd: "..",
  env,
  stdio: ["ignore", "ignore", "inherit"],
});

let failed = false;
const fail = (msg: string) => {
  console.error(`FAIL demo: ${msg}`);
  failed = true;
};

try {
  for (let i = 0; ; i++) {
    try {
      if ((await fetch(`${base}/api/health`)).ok) break;
    } catch {
      /* not up yet */
    }
    if (i > 60) throw new Error("the demo server did not start");
    await new Promise((r) => setTimeout(r, 500));
  }
  const browser = await webkit.launch();
  for (const scheme of ["light", "dark"]) {
    const ctx = await browser.newContext({ colorScheme: scheme, viewport: { width: 1280, height: 860 } });
    // the demo's cookie is Secure, which WebKit drops over plain http: sign one as the server
    // would (session.sign), so this run keeps one visitor
    const body = `${randomBytes(16).toString("hex")}.0`;
    const cookie = `${body}.${createHmac("sha256", env.FA_DEMO_SECRET).update(body).digest("hex")}`;
    await ctx.addCookies([{ name: "fa_demo", value: cookie, url: base }]);
    const page = await ctx.newPage();
    const art: string[] = [];
    page.on("request", (r: { url(): string }) => {
      if (r.url().includes("/api/card-art")) art.push(r.url());
    });
    await page.goto(`${base}/app`);
    await page.getByLabel("Net").waitFor();
    await page.waitForTimeout(1200);

    if (!(await page.getByText("Synthetic data. Nothing here is real.").isVisible())) fail(`${scheme}: no banner`);
    // privacy mode is on by default in the demo
    if (!(await page.evaluate(() => document.documentElement.hasAttribute("data-private")))) fail(`${scheme}: privacy mode is off`);

    // the page's cookie is this visitor's: the API answers for the same ledger
    const api = async (path: string) => (await page.request.get(`${base}${path}`)).json();
    const net = async () => (await page.getByLabel("Net", { exact: true }).first().innerText()).replace(/\s+/g, "");
    const donut = async () => (await page.getByRole("region", { name: "Where it went" }).innerText()).replace(/\s+/g, "");
    const summary = await api("/api/summary");
    for (const [label, minor, sign] of [
      ["Money in", summary.money_in.minor, false],
      ["Money out", summary.money_out.minor, false],
      ["Net", summary.net.minor, true],
    ] as const) {
      const got = (await page.getByLabel(label, { exact: true }).first().innerText()).replace(/\s+/g, "");
      if (!got.includes(gbp(minor, sign))) fail(`${scheme}: ${label} shows ${got}, API says ${gbp(minor, sign)}`);
    }
    const accounts = await api("/api/accounts");
    if (accounts.length < 3) fail(`${scheme}: ${accounts.length} accounts, want 3 or more`);
    if (shots) await page.screenshot({ path: `${shots}/demo-overview-${scheme}.png`, fullPage: true });

    // a change, then Reset: the Persona's first figures come back
    const first = { net: await net(), donut: await donut() };
    const { items } = await api(`/api/transactions?month=${summary.month}&limit=50`);
    const biggest = items.filter((t: { amount: { minor: number } }) => t.amount.minor < 0).sort((a: { amount: { minor: number } }, b: { amount: { minor: number } }) => a.amount.minor - b.amount.minor)[0];
    const moved = await page.request.post(`${base}/api/corrections`, {
      headers: { origin: base },
      data: { transaction_id: biggest.id, category: ["Travel", "Holidays"] },
    });
    if (!moved.ok()) fail(`${scheme}: the correction was refused (${moved.status()})`);
    await page.reload();
    await page.getByLabel("Net").waitFor();
    await page.waitForTimeout(1200);
    if ((await donut()) === first.donut) fail(`${scheme}: the change did not show`);
    const reset = page.waitForResponse((r: { url(): string }) => r.url().endsWith("/api/demo/reset"));
    await page.getByRole("button", { name: "Reset" }).click();
    const done = await reset;
    if (!done.ok()) fail(`${scheme}: Reset answered ${done.status()}`);
    await page.waitForLoadState("load"); // Reset reloads the page
    await page.getByLabel("Net").waitFor();
    await page.waitForTimeout(1200);
    const after = { net: await net(), donut: await donut() };
    if (after.donut !== first.donut || after.net !== first.net)
      fail(`${scheme}: Reset did not bring back the first figures (${first.donut} then ${after.donut})`);

    if (art.length) fail(`${scheme}: ${art.length} requests to /api/card-art`);
    await ctx.close();
  }
  await browser.close();
} finally {
  server.kill();
  rmSync(dir, { recursive: true, force: true });
}
if (failed) process.exit(1);
console.log("smoke:demo: banner shown, totals match the API, Reset restores, no card art");
