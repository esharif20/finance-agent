/* The connection map: every part and what it touches. Hover or focus a part to light its
   neighbours; select it to read its decision. The layout is fixed in graph.ts; a phone draws it
   turned on its side, with labels only where they fit. */
import { useState } from "react";
import { COLOUR, DOTS, type Dot, EDGES, H, NEIGHBOURS, W } from "./graph";
import { GROUPS, NODES } from "./nodes";
import { Detail } from "./parts";

const byId = Object.fromEntries(DOTS.map((d) => [d.id, d]));

export default function ArchMap({ wide }: { wide: boolean }) {
  const [hot, setHot] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const focus = hot ?? sel;
  const near = focus ? NEIGHBOURS[focus] : null;
  const shown = NODES.find((n) => n.id === sel);
  const told = NODES.find((n) => n.id === focus);
  // a phone turns the map on its side so it reads top to bottom
  const at = (d: Dot): [number, number] => (wide ? [d.x, d.y] : [d.y, d.x]);
  const r = wide ? 13 : 17;

  const pick = (id: string) => {
    setSel((s) => (s === id ? null : id));
    if (!wide) setHot(null);
  };

  return (
    <section className="ar-map" aria-labelledby="ar-h-map" onMouseLeave={() => setHot(null)}>
      <div className="ar-map-body" data-selected={sel ? true : undefined}>
        <div className="ar-map-head">
          <h2 id="ar-h-map">Thirty parts, one map</h2>
          <p className="ar-map-tell" aria-live="polite">
            {told ? (
              <>
                <strong>{told.title}.</strong> {told.short}. Touches {(NEIGHBOURS[told.id]?.size ?? 1) - 1}{" "}
                {(NEIGHBOURS[told.id]?.size ?? 1) - 1 === 1 ? "part" : "parts"}.
              </>
            ) : (
              "The bright line is the question's path. Point at any part to see what it touches, and select it to read the decision."
            )}
          </p>
        </div>
        <svg
          className="ar-graph"
          viewBox={wide ? `0 0 ${W} ${H}` : `-30 -20 ${H + 60} ${W + 40}`}
          role="group"
          aria-label="Map of the Assistant's parts and how they connect"
          data-focus={focus ? true : undefined}
          onKeyDown={(e) => e.key === "Escape" && (setSel(null), setHot(null))}
        >
          <g className="ar-edges" aria-hidden="true">
            {EDGES.map((e) => {
              const a = byId[e.a];
              const b = byId[e.b];
              if (!a || !b) return null;
              const [x1, y1] = at(a);
              const [x2, y2] = at(b);
              const on = focus != null && (e.a === focus || e.b === focus);
              const other = e.a === focus ? b : a;
              return (
                <g key={`${e.a}-${e.b}`} data-on={on || undefined} data-path={e.path || undefined}>
                  <line x1={x1} y1={y1} x2={x2} y2={y2} style={on ? { stroke: COLOUR[other.group] } : undefined} />
                  {e.path && <line className="ar-flowline" x1={x1} y1={y1} x2={x2} y2={y2} />}
                </g>
              );
            })}
          </g>
          <g className="ar-dots">
            {DOTS.map((d, i) => {
              const [x, y] = at(d);
              const node = NODES.find((n) => n.id === d.id);
              const dim = near != null && !near.has(d.id);
              const label = wide || d.group === "flow" || (near?.has(d.id) ?? false);
              return (
                <g
                  key={d.id}
                  className="ar-dot"
                  transform={`translate(${x} ${y})`}
                  role="button"
                  tabIndex={0}
                  aria-label={node?.title}
                  aria-pressed={sel === d.id}
                  data-dim={dim || undefined}
                  data-sel={sel === d.id || undefined}
                  data-group={d.group}
                  style={{ "--i": i } as React.CSSProperties}
                  onMouseEnter={() => setHot(d.id)}
                  onFocus={() => setHot(d.id)}
                  onBlur={() => setHot(null)}
                  onClick={() => pick(d.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      pick(d.id);
                    }
                  }}
                >
                  <circle className="ar-hit" r={wide ? 34 : 46} />
                  <g className="ar-pop">
                    {d.group === "flow" && <circle className="ar-halo" r={r + 7} />}
                    <circle className="ar-disc" r={r} fill={COLOUR[d.group]} />
                    {label && (
                      <text y={r + (wide ? 24 : 30)} textAnchor="middle">
                        {d.label}
                      </text>
                    )}
                  </g>
                </g>
              );
            })}
          </g>
        </svg>
        <div className="ar-map-side">
          {shown ? (
            <>
              <button type="button" className="ar-pill ar-back" onClick={() => setSel(null)}>
                Back to the chapters
              </button>
              <Detail node={shown} id={`ar-m-${shown.id}`} />
            </>
          ) : (
            <nav className="ar-toc" aria-label="On this page">
              <ul>
                {GROUPS.map((g) => (
                  <li key={g.id}>
                    <span className="ar-swatch" style={{ background: COLOUR[g.id] }} aria-hidden="true" />
                    <a href={g.id === "flow" ? "#flow" : `#ar-${g.id}`}>{g.title}</a>
                    <span>{NODES.filter((n) => n.group === g.id).length}</span>
                  </li>
                ))}
                <li>
                  <span className="ar-swatch ar-swatch-next" aria-hidden="true" />
                  <a href="#ar-next">What's next</a>
                </li>
              </ul>
            </nav>
          )}
        </div>
      </div>
    </section>
  );
}
