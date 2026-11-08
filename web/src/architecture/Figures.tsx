/* Small diagrams that sit beside a chapter's text. Drawn at about phone size, so the type stays
   legible when they scale. */
import { useId } from "react";

function Arrow({ id }: { id: string }) {
  return (
    <marker id={id} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse">
      <path d="M0 0 L10 5 L0 10 z" fill="currentColor" />
    </marker>
  );
}

function Box({ x, y, w, h, lines, solid }: { x: number; y: number; w: number; h: number; lines: string[]; solid?: boolean }) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={12} className={solid ? "ar-fig-solid" : "ar-fig-box"} />
      {lines.map((l, i) => (
        <text
          key={l}
          x={x + 12}
          y={y + h / 2 + (i - (lines.length - 1) / 2) * 16 + 5}
          className={i === 0 ? "ar-fig-strong" : "ar-fig-soft"}
          fill={solid ? "var(--field)" : "currentColor"}
        >
          {l}
        </text>
      ))}
    </g>
  );
}

/** The egress boundary: three hosts, each from one module, and nothing else leaves. */
export function EgressFigure() {
  const m = useId();
  return (
    <figure className="ar-figure">
      <svg viewBox="0 0 370 260" role="img" aria-label="The app on the Mac reaches Ollama on localhost; from ingest, one read-only SimpleFIN host; from the gateway, Gemini with placeholders only. Any other host is refused.">
        <defs>
          <Arrow id={m} />
        </defs>
        <rect x={4} y={4} width={232} height={252} rx={20} className="ar-fig-zone" />
        <text x={20} y={30} className="ar-fig-strong">
          This Mac
        </text>
        <Box x={20} y={50} w={150} h={44} lines={["Finance Agent"]} solid />
        <Box x={20} y={190} w={160} h={48} lines={["Ollama", "localhost only"]} />
        <g fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round">
          <path d="M70 94 V184" markerEnd={`url(#${m})`} />
          <path d="M170 64 C210 64 220 44 254 44" markerEnd={`url(#${m})`} />
          <path d="M170 80 C214 80 214 122 254 122" markerEnd={`url(#${m})`} />
          <path d="M150 94 C150 170 190 206 222 206" strokeDasharray="5 6" />
          <path d="M228 194 l16 24 M244 194 l-16 24" strokeWidth={3} />
        </g>
        <Box x={258} y={20} w={108} h={50} lines={["SimpleFIN", "ingest, read-only"]} />
        <Box x={258} y={98} w={108} h={50} lines={["Gemini", "gateway, opt-in"]} />
        <Box x={258} y={182} w={108} h={50} lines={["Any other host", "refused"]} />
      </svg>
      <figcaption>Each host is reachable from one module only, and a test scans every source file for anything else.</figcaption>
    </figure>
  );
}

/** Which model words the reply: the router keeps it local unless the user opted in. */
export function ModelsFigure() {
  const m = useId();
  return (
    <figure className="ar-figure">
      <svg viewBox="0 0 370 210" role="img" aria-label="A question goes to the router. By default the local model answers. In a conversation the user opted in to, the gateway redacts the turn to placeholders and Gemini words the reply.">
        <defs>
          <Arrow id={m} />
        </defs>
        <Box x={4} y={80} w={92} h={44} lines={["Question"]} />
        <Box x={122} y={80} w={86} h={44} lines={["Router"]} solid />
        <Box x={236} y={14} w={130} h={50} lines={["Local model", "the default"]} />
        <Box x={236} y={142} w={130} h={50} lines={["Gemini", "opt-in, per chat"]} />
        <g fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round">
          <path d="M96 102 H116" markerEnd={`url(#${m})`} />
          <path d="M208 92 C222 92 214 39 230 39" markerEnd={`url(#${m})`} />
          <path d="M208 112 C222 112 214 167 230 167" markerEnd={`url(#${m})`} strokeDasharray="5 6" />
        </g>
        <text x={240} y={124} className="ar-fig-soft" fill="currentColor">
          placeholders only
        </text>
      </svg>
      <figcaption>The dashed path runs only after the tools ran on the Mac, and only placeholders cross it.</figcaption>
    </figure>
  );
}
