// Smoke test: run `fa`, open the Overview in WebKit, assert the totals match the API.
//   FA_DB_PATH=<synthetic db> pnpm --dir web run smoke [screens-dir]
// Needs `pnpm build` first and a WebKit install of the global Playwright CLI.
// Not part of CI (no browser there); never opens a real browser window of yours:
// FA_NO_BROWSER is set and `fa stop` always runs.
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";

const require = createRequire(import.meta.url);
const globalRoot = execFileSync("npm", ["root", "-g"]).toString().trim();
const { webkit } = require(process.env.PLAYWRIGHT_MODULE ?? `${globalRoot}/playwright`);

const port = process.env.FA_PORT ?? "8791";
const env = { ...process.env, FA_PORT: port, FA_NO_BROWSER: "1" };
const fa = (...args) => execFileSync("uv", ["run", "fa", ...args], { env, cwd: "..", stdio: "inherit" });
const base = `http://127.0.0.1:${port}`;
const shots = process.argv[2];
if (shots) mkdirSync(shots, { recursive: true });

const gbp = (minor, sign) => {
  const abs = Math.abs(minor);
  const s = `£${Math.floor(abs / 100).toLocaleString("en-GB")}.${String(abs % 100).padStart(2, "0")}`;
  return minor < 0 ? `−${s}` : sign && minor > 0 ? `+${s}` : s;
};

let failed = false;
fa();
try {
  const summary = await (await fetch(`${base}/api/summary`)).json();
  const accounts = await (await fetch(`${base}/api/accounts`)).json();
  const spending = await (await fetch(`${base}/api/spending`)).json();
  const months = await (await fetch(`${base}/api/months`)).json();
  const browser = await webkit.launch();
  for (const [scheme, name, viewport] of [
    ["light", "desktop", { width: 1280, height: 860 }],
    ["dark", "desktop", { width: 1280, height: 860 }],
    ["light", "phone", { width: 390, height: 844 }],
    ["dark", "phone", { width: 390, height: 844 }],
    ["light", "narrow", { width: 375, height: 812 }],
    ["dark", "narrow", { width: 375, height: 812 }],
  ]) {
    const page = await browser.newPage({ colorScheme: scheme, viewport });
    await page.goto(base);
    await page.getByLabel("Net").waitFor();
    await page.waitForTimeout(900); // count-up and strip finish; read the settled figures
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (overflow > 0) {
      console.error(`FAIL ${scheme} ${name}: page scrolls sideways by ${overflow}px`);
      failed = true;
    }
    const text = async (label) => (await page.getByLabel(label, { exact: true }).first().innerText()).replace(/\s+/g, "");
    const want = [
      ["Money in", gbp(summary.money_in.minor)],
      ["Money out", gbp(summary.money_out.minor)],
      ["Net", gbp(summary.net.minor, true)],
    ];
    for (const [label, expected] of want) {
      const got = await text(label);
      if (!got.includes(expected)) {
        console.error(`FAIL ${scheme} ${name}: ${label} shows ${got}, API says ${expected}`);
        failed = true;
      }
    }
    const donut = (await page.getByRole("region", { name: "Where it went" }).innerText()).replace(/\s+/g, "");
    if (!donut.includes(gbp(spending.total.minor))) {
      console.error(`FAIL ${scheme} ${name}: donut total is not ${gbp(spending.total.minor)}`);
      failed = true;
    }
    for (const c of spending.categories) {
      if (!donut.includes(gbp(c.amount.minor))) {
        console.error(`FAIL ${scheme} ${name}: ${c.name} amount missing from the legend`);
        failed = true;
      }
    }
    const body = await page.locator("main").innerText();
    for (const a of accounts) {
      if (!body.includes(gbp(a.bank_balance.minor).replace(/\.\d\d$/, ""))) {
        console.error(`FAIL ${scheme} ${name}: balance of ${a.masked_number} missing`);
        failed = true;
      }
      const row = await page.getByRole("button", { name: new RegExp(a.masked_number) }).first().textContent(); // textContent: back cards hide their status visually, not from screen readers
      if (!row.includes(a.gaps.length ? "Find it" : "Matches ledger")) {
        console.error(`FAIL ${scheme} ${name}: status of ${a.masked_number} wrong`);
        failed = true;
      }
    }
    if (shots) await page.screenshot({ path: `${shots}/overview-${scheme}-${name}.png`, fullPage: true });
    if (months.length > 1) {
      // step back a month: every Overview figure must follow the API for that month
      const prev = months[1];
      const sum = await (await fetch(`${base}/api/summary?month=${prev}`)).json();
      await page.getByRole("button", { name: "Previous month" }).click();
      await page.waitForTimeout(700);
      const got = await text("Net");
      if (!got.includes(gbp(sum.net.minor, true))) {
        console.error(`FAIL ${scheme} ${name}: after switching to ${prev}, Net shows ${got}`);
        failed = true;
      }
      if (shots) await page.screenshot({ path: `${shots}/overview-${scheme}-${name}-prev.png`, fullPage: true });
    }
    // privacy mode: H blurs every amount and the page keeps its width
    await page.keyboard.press("h");
    const unblurred = await page.$$eval(".money", (els) => els.filter((e) => getComputedStyle(e).filter === "none").length);
    if (unblurred) {
      console.error(`FAIL ${scheme} ${name}: ${unblurred} amounts not blurred in privacy mode`);
      failed = true;
    }
    if (shots) await page.screenshot({ path: `${shots}/overview-${scheme}-${name}-private.png`, fullPage: true });
    await page.close();
  }
  await browser.close();
} finally {
  fa("stop");
}
if (failed) process.exit(1);
console.log("smoke: Overview totals match the API");
