/* A hand-drawn flowchart in the page's style: boxes, arrows, an optional dashed boundary. Edges draw
   in when the chart reaches the screen, and with motion allowed a dot rides the active path on a
   loop. Hover or focus a box to light what it connects to and read one line about it. Phones and
   reduced motion get the same chart, drawn and still. */
import { animate, useInView } from "motion/react";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";

export interface CNode {
  id: string;
  x: number;
  y: number;
  w: number;
  h?: number;
  label: string;
  sub?: string;
  tip: string;
  solid?: boolean;
  /** outside the boundary, or not built yet: drawn as an outline */
  planned?: boolean;
  outside?: boolean;
}
export interface CEdge {
  from: string;
  to: string;
  label?: string;
  dashed?: boolean;
  /** custom path, for loops and detours */
  d?: string;
  /** leave and enter by the sides (h) or the top and bottom (v), instead of the nearer pair */
  via?: "h" | "v";
}
export interface CZone {
  x: number;
  y: number;
  w: number;
  h: number;
  label: string;
  /** not built yet: drawn dashed, with a Planned pill */
  planned?: boolean;
}
export interface Layout {
  vb: [number, number];
  nodes: CNode[];
  zones?: CZone[];
}

const H = 54;

function geometry(a: CNode, b: CNode, via?: "h" | "v") {
  const ah = a.h ?? H;
  const bh = b.h ?? H;
  const ca = { x: a.x + a.w / 2, y: a.y + ah / 2 };
  const cb = { x: b.x + b.w / 2, y: b.y + bh / 2 };
  const dx = cb.x - ca.x;
  const dy = cb.y - ca.y;
  if (via ? via === "h" : Math.abs(dx) * 0.6 >= Math.abs(dy)) {
    const x1 = dx > 0 ? a.x + a.w : a.x;
    const x2 = dx > 0 ? b.x - 4 : b.x + b.w + 4;
    const m = (x1 + x2) / 2;
    return { d: `M${x1} ${ca.y} C${m} ${ca.y} ${m} ${cb.y} ${x2} ${cb.y}`, mx: m, my: (ca.y + cb.y) / 2 };
  }
  const y1 = dy > 0 ? a.y + ah : a.y;
  const y2 = dy > 0 ? b.y - 4 : b.y + bh + 4;
  const m = (y1 + y2) / 2;
  return { d: `M${ca.x} ${y1} C${ca.x} ${m} ${cb.x} ${m} ${cb.x} ${y2}`, mx: (ca.x + cb.x) / 2, my: m };
}

export default function Chart({
  title,
  layout,
  edges,
  active = [],
  live,
  extra,
  className,
}: {
  title: string;
  layout: Layout;
  edges: CEdge[];
  /** indexes into `edges`, in the order the dot rides them */
  active?: number[];
  live: boolean;
  extra?: ReactNode;
  className?: string;
}) {
  const marker = useId();
  const box = useRef<HTMLDivElement>(null);
  const dot = useRef<SVGCircleElement>(null);
  const paths = useRef<(SVGPathElement | null)[]>([]);
  const seen = useInView(box, { once: true, amount: 0.35 });
  const onScreen = useInView(box, { amount: 0.2 });
  const [hot, setHot] = useState<string | null>(null);
  const [W, Hh] = layout.vb;
  const byId = Object.fromEntries(layout.nodes.map((n) => [n.id, n]));
  const drawn = edges.map((e) => {
    const a = byId[e.from];
    const b = byId[e.to];
    return a && b ? { e, ...geometry(a, b, e.via), d2: e.d } : null;
  });

  // one dot, one driver: ride each active edge in turn, rest, repeat
  useEffect(() => {
    if (!live || !seen || !onScreen || active.length === 0) return;
    let stopped = false;
    let run: { stop: () => void } | null = null;
    const ride = async () => {
      while (!stopped) {
        for (const i of active) {
          const p = paths.current[i];
          const c = dot.current;
          if (stopped || !p || !c || typeof p.getTotalLength !== "function") return;
          const len = p.getTotalLength();
          c.style.opacity = "1";
          run = animate(0, len, {
            duration: 0.75,
            ease: "easeInOut",
            onUpdate: (l: number) => {
              const pt = p.getPointAtLength(l);
              c.setAttribute("transform", `translate(${pt.x} ${pt.y})`);
            },
          });
          await run;
        }
        if (dot.current) dot.current.style.opacity = "0";
        await new Promise((r) => setTimeout(r, 1400));
      }
    };
    const t = setTimeout(ride, 900);
    return () => {
      stopped = true;
      clearTimeout(t);
      run?.stop();
    };
  }, [live, seen, onScreen, layout]);

  const near = new Set<string>();
  if (hot) {
    near.add(hot);
    for (const e of edges) {
      if (e.from === hot) near.add(e.to);
      if (e.to === hot) near.add(e.from);
    }
  }
  const hotNode = hot ? byId[hot] : null;

  return (
    <div className={`ar-chart ${className ?? ""}`} ref={box} data-in={seen || !live || undefined} data-hot={hot ? true : undefined}>
      <svg viewBox={`0 0 ${W} ${Hh}`} role="group" aria-label={title} onMouseLeave={() => setHot(null)}>
        <defs>
          <marker id={marker} viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
            <path d="M0 0 L10 5 L0 10 z" fill="currentColor" />
          </marker>
        </defs>
        {layout.zones?.map((z) => (
          <g key={z.label} className="ar-ch-zone" data-planned={z.planned || undefined}>
            <rect x={z.x} y={z.y} width={z.w} height={z.h} rx={22} />
            <text x={z.x + 16} y={z.y + 26}>
              {z.label}
            </text>
            {z.planned && (
              <g className="ar-ch-pill" transform={`translate(${z.x + z.w - 92} ${z.y + 10})`}>
                <rect width={78} height={24} rx={12} />
                <text x={39} y={16.5} textAnchor="middle">
                  Planned
                </text>
              </g>
            )}
          </g>
        ))}
        <g className="ar-ch-edges" aria-hidden="true">
          {drawn.map((g, i) =>
            g ? (
              <g
                key={`${g.e.from}-${g.e.to}`}
                className="ar-ch-edge"
                data-dashed={g.e.dashed || undefined}
                data-on={hot != null && (g.e.from === hot || g.e.to === hot) ? true : undefined}
                style={{ "--e": i } as React.CSSProperties}
              >
                <path
                  ref={(el) => {
                    paths.current[i] = el;
                  }}
                  d={g.d2 ?? g.d}
                  pathLength={g.e.dashed ? undefined : 1}
                  markerEnd={`url(#${marker})`}
                />
                {g.e.label && (
                  <text x={g.mx} y={g.my - 7} textAnchor="middle">
                    {g.e.label}
                  </text>
                )}
              </g>
            ) : null,
          )}
        </g>
        {layout.nodes.map((n, i) => (
          <g
            key={n.id}
            className="ar-ch-node"
            tabIndex={0}
            role="img"
            aria-label={`${n.label}. ${n.tip}`}
            data-solid={n.solid || undefined}
            data-outside={n.outside || n.planned || undefined}
            data-dim={hot != null && !near.has(n.id) ? true : undefined}
            style={{ "--n": i } as React.CSSProperties}
            onMouseEnter={() => setHot(n.id)}
            onFocus={() => setHot(n.id)}
            onBlur={() => setHot(null)}
            onClick={() => setHot((h) => (h === n.id ? null : n.id))}
          >
            <rect x={n.x} y={n.y} width={n.w} height={n.h ?? H} rx={14} />
            <text x={n.x + 14} y={n.y + (n.sub ? 23 : (n.h ?? H) / 2 + 6)} className="ar-ch-label">
              {n.label}
            </text>
            {n.sub && (
              <text x={n.x + 14} y={n.y + 42} className="ar-ch-sub">
                {n.sub}
              </text>
            )}
          </g>
        ))}
        {extra}
        <circle ref={dot} className="ar-ch-dot" r={6} style={{ opacity: 0 }} aria-hidden="true" />
      </svg>
      {hotNode && (
        <p
          className="ar-ch-tip"
          style={{
            // kept inside the chart: the note is at most 260px wide
            left: `clamp(130px, ${((hotNode.x + hotNode.w / 2) / W) * 100}%, calc(100% - 130px))`,
            top: `${((hotNode.y + (hotNode.h ?? H)) / Hh) * 100}%`,
          }}
          aria-hidden="true"
        >
          {hotNode.tip}
        </p>
      )}
    </div>
  );
}
