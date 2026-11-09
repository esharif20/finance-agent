import { useEffect, useRef, useState } from "react";
import { formatMoney } from "@/lib/money";
import { ACCOUNTS } from "./accounts.fixture";
import { Corners } from "./fx";
import { digitsIn, Tally } from "./Preloader";

const MIN_MS = 800;
const MAX_MS = 8000; // a cold demo server can take a few seconds; past this, go anyway
const TOTAL = formatMoney({ minor: ACCOUNTS.balance, currency: "GBP" });
const DIGITS = digitsIn(TOTAL);

/** The demo's first request: it makes the visitor's synthetic ledger and warms the API, so the
    app opens on data. Resolves either way; the app handles its own errors. */
export const warm = () =>
  fetch("/api/accounts", { credentials: "same-origin" }).then(
    () => undefined,
    () => undefined,
  );

/** "Open the demo": the tally again, in reverse. The water rises over the household's total
    while the ledger is made, and the app opens when it is ready (at least 0.8 s, so it reads as a
    beat, not a flicker). Under reduced motion, a short fade. */
export function Opener({ still, onGone, go = (u: string) => window.location.assign(u) }: { still: boolean; onGone: () => void; go?: (u: string) => void }) {
  const root = useRef<HTMLDivElement>(null);
  const [landed, setLanded] = useState(still ? DIGITS : 0);
  const [full, setFull] = useState(false);
  const leave = useRef(go);
  leave.current = go;

  useEffect(() => {
    const t0 = performance.now();
    let ready = false;
    let left = false;
    const open = () => {
      if (left) return;
      left = true;
      setFull(true);
      window.setTimeout(() => leave.current("/app"), still ? 120 : 260);
    };
    const check = () => {
      if (ready && performance.now() - t0 >= MIN_MS) open();
    };
    warm().then(() => {
      ready = true;
      check();
    });
    const min = window.setTimeout(check, MIN_MS);
    const cap = window.setTimeout(open, MAX_MS);
    const wheels = still ? [] : Array.from({ length: DIGITS }, (_, d) => window.setTimeout(() => setLanded(d + 1), 120 + d * 60));
    // coming back with the back button restores this page from the cache: drop the curtain
    const show = (e: PageTransitionEvent) => e.persisted && onGone();
    window.addEventListener("pageshow", show);
    root.current?.focus();
    return () => {
      window.clearTimeout(min);
      window.clearTimeout(cap);
      wheels.forEach((w) => window.clearTimeout(w));
      window.removeEventListener("pageshow", show);
    };
  }, [still, onGone]);

  return (
    <div ref={root} className="op" data-still={still ? "" : undefined} data-full={full ? "" : undefined} role="status" tabIndex={-1}>
      <div className="op-water" />
      <Corners tl="Opening the demo" tr="The household's total" bl="Synthetic data" br="Nothing you do is kept" />
      <div className="op-body">
        <Tally text={TOTAL} landed={landed} className="pl-count op-count" />
        <p className="op-note">Making your copy of the made-up household…</p>
      </div>
    </div>
  );
}
