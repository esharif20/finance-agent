import { useInView } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { Magnetic, Roll, useStill } from "../fx";
import { TERMINAL } from "./terminal.fixture";

// `fa demo`'s own lines (cli/demo.py), with the address it prints for a local run
const DEMO_STATUS = "demo · synthetic data · a random persona · http://127.0.0.1:8765/";

type Step =
  | { kind: "cmd"; text: string }
  | { kind: "out"; lines: readonly string[]; dim?: boolean }
  | { kind: "banner" };

export const SCRIPT: Step[] = [
  { kind: "cmd", text: "fa status" },
  { kind: "out", lines: TERMINAL.output.status },
  { kind: "cmd", text: "fa month" },
  { kind: "out", lines: TERMINAL.output.month },
  { kind: "cmd", text: "fa subs" },
  { kind: "out", lines: TERMINAL.output.subs },
  { kind: "cmd", text: "fa demo" },
  { kind: "out", lines: ["Building the synthetic personas…"] },
  { kind: "banner" },
  { kind: "out", lines: [TERMINAL.tagline], dim: true },
  { kind: "out", lines: [DEMO_STATUS, "Synthetic data only. Ctrl-C stops the demo and deletes it.", "opening the demo…"] },
];

/** Paints banner frame i on a canvas: one canvas pixel per banner pixel, so it matches the
    terminal's half-blocks exactly once the canvas is scaled to 1ch by half a line. */
function paint(canvas: HTMLCanvasElement | null, i: number) {
  const g = canvas?.getContext("2d");
  const f = TERMINAL.frames[i];
  if (!g || !f) return;
  g.clearRect(0, 0, TERMINAL.width, TERMINAL.height);
  for (let p = 0; p < TERMINAL.width * TERMINAL.height; p++) {
    const code = f.slice(p * 2, p * 2 + 2);
    if (code === "..") continue;
    g.fillStyle = TERMINAL.palette[parseInt(code, 36)] ?? "#000";
    g.fillRect(p % TERMINAL.width, Math.floor(p / TERMINAL.width), 1, 1);
  }
}

function Banner({ play }: { play: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    if (!play) {
      paint(ref.current, TERMINAL.frames.length - 1);
      return;
    }
    let i = 0;
    paint(ref.current, 0);
    const id = window.setInterval(() => {
      i += 1;
      paint(ref.current, i);
      if (i >= TERMINAL.frames.length - 1) window.clearInterval(id);
    }, TERMINAL.frameMs);
    return () => window.clearInterval(id);
  }, [play]);
  return <canvas ref={ref} className="tm-banner" width={TERMINAL.width} height={TERMINAL.height} style={{ width: `${TERMINAL.width}ch`, height: `${TERMINAL.height * 0.725}em` }} aria-hidden="true" />;
}

type Pos = { step: number; at: number }; // `at`: characters typed, or lines shown

const DONE: Pos = { step: SCRIPT.length, at: 0 };

function Screen({ pos, playing }: { pos: Pos; playing: boolean }) {
  const rows: React.ReactNode[] = [];
  SCRIPT.forEach((s, i) => {
    if (i > pos.step) return;
    const partial = i === pos.step;
    if (s.kind === "cmd") {
      const text = partial ? s.text.slice(0, pos.at) : s.text;
      rows.push(
        <div key={i} className="tm-cmd">
          <span className="tm-prompt">~ $</span> {text}
          {partial && <span className="tm-caret" />}
        </div>,
      );
    } else if (s.kind === "out") {
      const lines = partial ? s.lines.slice(0, pos.at) : s.lines;
      lines.forEach((l, j) =>
        rows.push(
          <div key={`${i}-${j}`} className={s.dim ? "tm-dim" : undefined}>
            {l || " "}
          </div>,
        ),
      );
    } else if (!partial || pos.at > 0) {
      rows.push(<Banner key={i} play={playing} />);
    }
  });
  if (pos.step >= SCRIPT.length)
    rows.push(
      <div key="end" className="tm-cmd">
        <span className="tm-prompt">~ $</span> <span className="tm-caret tm-caret-idle" />
      </div>,
    );
  return <>{rows}</>;
}

/** The terminal chapter: `fa` played as it runs, triggered once as the window scrolls in.
    Under reduced motion it shows the last frame, everything printed. */
export function Terminal() {
  const still = useStill();
  const win = useRef<HTMLDivElement>(null);
  const seen = useInView(win, { once: true, amount: 0.4 });
  const [pos, setPos] = useState<Pos>(still ? DONE : { step: 0, at: 0 });
  const [run, setRun] = useState(0);
  const body = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (still || !seen) return;
    let t = 0;
    let cur: Pos = { step: 0, at: 0 };
    setPos(cur);
    const tick = () => {
      const s = SCRIPT[cur.step];
      if (!s) return;
      let wait = 0;
      if (s.kind === "cmd") {
        if (cur.at < s.text.length) {
          cur = { step: cur.step, at: cur.at + 1 };
          wait = 55 + Math.random() * 45;
        } else {
          cur = { step: cur.step + 1, at: 0 };
          wait = 260;
        }
      } else if (s.kind === "out") {
        if (cur.at < s.lines.length) {
          cur = { step: cur.step, at: cur.at + 1 };
          wait = 28;
        } else {
          cur = { step: cur.step + 1, at: 0 };
          wait = 650;
        }
      } else {
        cur = cur.at === 0 ? { step: cur.step, at: 1 } : { step: cur.step + 1, at: 0 };
        wait = cur.at === 1 ? TERMINAL.frames.length * TERMINAL.frameMs + 120 : 0;
      }
      setPos(cur);
      t = window.setTimeout(tick, wait);
    };
    t = window.setTimeout(tick, 400);
    return () => window.clearTimeout(t);
  }, [still, seen, run]);

  // keep the newest line in view, like a terminal does
  useEffect(() => {
    const b = body.current;
    if (b) b.scrollTop = b.scrollHeight;
  }, [pos]);

  const done = pos.step >= SCRIPT.length;
  return (
    <section className="tm" aria-labelledby="tm-title">
      <div className="lp-wrap tm-grid">
        <div className="tm-copy">
          <h2 id="tm-title" className="lp-h2">
            Or run it from the terminal.
          </h2>
          <p className="tm-text">
            Everything the app shows, <code>fa</code> prints. This is its output on one made-up household, then{" "}
            <code>fa demo</code> opens the same app in your browser.
          </p>
          <Magnetic href="/app" className="lp-pill lp-pill-primary lp-pill-large tm-cta">
            <Roll text="Open the demo" />
          </Magnetic>
        </div>
        <div ref={win} className="tm-window" data-ui="" data-done={done ? "" : undefined}>
          <div className="tm-bar">
            <span className="tm-dots" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            <span className="tm-title">fa — ~</span>
            {!still && (
              <button type="button" className="tm-replay" onClick={() => setRun((n) => n + 1)} disabled={!done}>
                Replay
              </button>
            )}
          </div>
          <div ref={body} className="tm-body" role="log" aria-label="The fa command line, run on a made-up ledger">
            <div className="tm-lines">
              <Screen pos={pos} playing={!still} />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
