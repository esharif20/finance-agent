/* A small floating pill on a long page: which chapter you are in, how far down the page you are,
   and a way back to the contents. It appears once the hero has scrolled away and leaves at the
   footer. ScrollTrigger reads the scroll (through Lenis when it runs); the bar's fill is written
   straight to its style, so scrolling never re-renders React. */
import { useRef, useState } from "react";
import { GROUPS } from "./nodes";
import { ScrollTrigger, useGSAP } from "./smooth";

const STOPS: [string, string][] = [
  ["flow", GROUPS[0]?.title ?? ""],
  ["ar-agent", "The agent"],
  ...GROUPS.slice(1).map((g): [string, string] => [`ar-${g.id}`, g.title]),
  ["ar-next", "What's next"],
];

export default function Wayfinder({ still }: { still: boolean }) {
  const root = useRef<HTMLAnchorElement>(null);
  const fill = useRef<HTMLSpanElement>(null);
  const [at, setAt] = useState(-1);
  const [shown, setShown] = useState(false);

  useGSAP(
    () => {
      ScrollTrigger.create({
        start: 0,
        end: "max",
        onUpdate: (s) => {
          if (fill.current) fill.current.style.transform = `scaleX(${s.progress})`;
        },
      });
      STOPS.forEach(([id], i) => {
        const el = document.getElementById(id);
        if (!el) return;
        ScrollTrigger.create({
          trigger: el,
          start: "top 55%",
          end: "bottom 55%",
          onToggle: (s) => s.isActive && setAt(i),
        });
      });
      const first = document.getElementById(STOPS[0]?.[0] ?? "");
      const foot = document.querySelector(".ar-foot");
      if (first)
        ScrollTrigger.create({
          trigger: first,
          start: "top 80%",
          endTrigger: foot ?? undefined,
          end: "top bottom",
          onToggle: (s) => setShown(s.isActive),
        });
      // the chapters above mount first, so positions are settled by now; refresh once for fonts
      void document.fonts?.ready.then(() => ScrollTrigger.refresh());
    },
    { dependencies: [] },
  );

  const stop = STOPS[at];
  return (
    <a
      ref={root}
      className="ar-way"
      href="#ar-toc"
      data-shown={shown || undefined}
      data-still={still || undefined}
      aria-label={stop ? `In ${stop[1]}. Back to the contents` : "Back to the contents"}
      tabIndex={shown ? 0 : -1}
      aria-hidden={!shown}
    >
      <span className="ar-way-n">
        {String(Math.max(1, at + 1)).padStart(2, "0")}
        <span>/{String(STOPS.length).padStart(2, "0")}</span>
      </span>
      <span className="ar-way-t" key={stop?.[0]}>
        {stop?.[1] ?? STOPS[0]?.[1]}
      </span>
      <span className="ar-way-bar" aria-hidden="true">
        <span ref={fill} />
      </span>
    </a>
  );
}

