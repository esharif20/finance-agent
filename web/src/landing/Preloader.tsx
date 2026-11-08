import { useEffect, useRef, useState } from "react";
import { formatMoney } from "@/lib/money";
import { OVERVIEW } from "./story/story.fixture";

const MIN_MS = 1200;
const MAX_MS = 2500;
const STILL_MAX_MS = 1200;
const FACES = ['900 1em "Red Hat Display Variable"', '400 1em "Red Hat Text Variable"'];

/** The figure the tally settles on: October's spend on the made-up ledger, as the page shows it. */
export const TALLY = formatMoney(OVERVIEW.spent);
const DIGITS = [...TALLY].filter((c) => /\d/.test(c)).length;

/** Real loading as 0..1: the two Red Hat faces (0.7) and the window load event, every chunk and
    stylesheet (0.3). Where the Font Loading API is missing, the faces count as loaded. */
function track(set: (f: number) => void) {
  let fonts = 0;
  let page = document.readyState === "complete";
  const emit = () => set(Math.min(1, fonts * 0.7 + (page ? 0.3 : 0)));
  const fs = typeof document.fonts?.load === "function" ? document.fonts : null;
  if (fs)
    for (const f of FACES)
      fs.load(f)
        .catch(() => [])
        .then(() => {
          fonts += 1 / FACES.length;
          emit();
        });
  else fonts = 1;
  const onLoad = () => {
    page = true;
    emit();
  };
  if (!page) window.addEventListener("load", onLoad, { once: true });
  emit();
  return () => window.removeEventListener("load", onLoad);
}

/** One odometer wheel: 0–9 once round, then up to the real digit. It starts at zero, rolls, and
    rests only on the true figure. */
function Wheel({ d, on }: { d: number; on: boolean }) {
  return (
    <span className="pl-wheel">
      <span className="pl-wheel-in" data-on={on ? "" : undefined} style={{ "--to": 10 + d } as React.CSSProperties}>
        {Array.from({ length: 11 + d }, (_, i) => (
          <span key={i}>{i % 10}</span>
        ))}
      </span>
    </span>
  );
}

function Tally({ landed }: { landed: number }) {
  let k = 0;
  return (
    <span className="pl-count">
      {[...TALLY].map((c, i) => {
        if (!/\d/.test(c))
          return (
            <span key={i} className="pl-sep">
              {c}
            </span>
          );
        const n = k++;
        return <Wheel key={i} d={Number(c)} on={n < landed} />;
      })}
    </span>
  );
}

/** The tally: the hero's own first frame. The water rises with real loading to the hero's seam
    while the odometer settles on October's spend, left to right; then the figure lifts away and
    the hero's type rises in over water that has not moved. natnael.space's reel load, re-made in
    the waterline. Shown once a session; under reduced motion, a short fade over the settled frame. */
export function Preloader({
  still,
  rest,
  onGo,
  onDone,
}: {
  still: boolean;
  rest: number;
  onGo: () => void;
  onDone: () => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const [landed, setLanded] = useState(still ? DIGITS : 0);
  const [out, setOut] = useState(false);
  const go = useRef(onGo);
  const done = useRef(onDone);
  go.current = onGo;
  done.current = onDone;

  useEffect(() => {
    let real = 0;
    let shown = still ? 1 : 0;
    let raf = 0;
    let left = false;
    let gone = 0;
    let hold = 0;
    const t0 = performance.now();
    // the 2.5 s cap runs from navigation, as the visitor feels it, but the tally always gets its 1.2 s
    const capAt = Math.max(MAX_MS - t0, MIN_MS);
    const seam = (p: number) => root.current?.style.setProperty("--seam", String(100 - p * (100 - rest)));
    const leave = () => {
      if (left) return;
      left = true;
      cancelAnimationFrame(raf);
      seam(1);
      setLanded(DIGITS);
      setOut(true);
      go.current();
      // the figure lifts (0.5s) and the sheet fades (0.4s after 0.25s); still: a 0.3s fade
      gone = window.setTimeout(() => done.current(), still ? 320 : 700);
    };
    const untrack = track((f) => {
      real = f;
      if (still && f >= 0.7) leave(); // fonts are in: fade straight away
    });
    seam(shown);
    const frame = (now: number) => {
      const t = now - t0;
      if (t >= capAt) return leave();
      const target = Math.min(real, t / MIN_MS);
      shown += (target - shown) * 0.14;
      if (target - shown < 0.002) shown = target;
      seam(shown);
      // the wheels land through the first 90% of the progress, so the last one settles as the water arrives
      setLanded(Math.min(DIGITS, Math.floor((shown / 0.9) * DIGITS)));
      if (shown >= 1 && !hold) hold = window.setTimeout(leave, 420); // a beat to read the figure
      raf = requestAnimationFrame(frame);
    };
    if (!still) raf = requestAnimationFrame(frame);
    const cap = window.setTimeout(leave, still ? STILL_MAX_MS : capAt); // rAF stalls in a background tab
    // a click or any key goes straight in, as natnael.space's does
    window.addEventListener("pointerdown", leave);
    window.addEventListener("keydown", leave);
    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(cap);
      window.clearTimeout(hold);
      window.clearTimeout(gone);
      untrack();
      window.removeEventListener("pointerdown", leave);
      window.removeEventListener("keydown", leave);
    };
  }, [still, rest]);

  return (
    <div
      ref={root}
      className="pl"
      aria-hidden="true"
      data-still={still ? "" : undefined}
      data-out={out ? "" : undefined}
      style={{ "--rest": rest } as React.CSSProperties}
    >
      <div className="pl-water" />
      {[false, true].map((under) => (
        <div key={String(under)} className={under ? "pl-plane pl-below" : "pl-plane pl-above"}>
          <div className="pl-mask">
            <Tally landed={landed} />
          </div>
          <p className="pl-note">spent in October on the made-up ledger</p>
        </div>
      ))}
    </div>
  );
}
