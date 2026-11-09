/* Pieces shared by the map, the question's path and the chapters. */
import { motion } from "motion/react";
import { type ReactNode, useEffect, useState } from "react";
import { type ArchNode, REPO, STATUS_LABEL, type Status } from "./nodes";

export const EASE = [0.22, 1, 0.36, 1] as const;

export function query(q: string) {
  return typeof window.matchMedia === "function" && window.matchMedia(q).matches;
}

/** Read once per mount, like the landing page, so a test can switch it between renders. */
export const useStill = () => useState(() => query("(prefers-reduced-motion: reduce)"))[0];

/** True while the media query matches, following changes. */
export function useMedia(q: string) {
  const [on, setOn] = useState(() => query(q));
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const m = window.matchMedia(q);
    const f = () => setOn(m.matches);
    m.addEventListener("change", f);
    return () => m.removeEventListener("change", f);
  }, [q]);
  return on;
}

export function Mark({ status }: { status: Status }) {
  return <span className={`ar-mark ar-mark-${status}`} aria-hidden="true" />;
}

export function StatusTag({ status }: { status: Status }) {
  return (
    <span className="ar-status">
      <Mark status={status} />
      {STATUS_LABEL[status]}
    </span>
  );
}

export function Detail({ node, id, visual }: { node: ArchNode; id: string; visual?: ReactNode }) {
  return (
    <motion.div
      id={id}
      key={node.id}
      className="ar-detail"
      role="region"
      aria-label={node.title}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.24, ease: EASE }}
    >
      <div className="ar-detail-head">
        <h3>{node.title}</h3>
        <StatusTag status={node.status} />
      </div>
      {visual && <div className="ar-detail-visual">{visual}</div>}
      <div className="ar-pair">
        <h4>Decision</h4>
        <p>{node.decision}</p>
      </div>
      <div className="ar-pair">
        <h4>Why</h4>
        <p>{node.why}</p>
      </div>
      {node.note && (
        <div className="ar-pair">
          <h4>Where it stands</h4>
          <p>{node.note}</p>
        </div>
      )}
      <div className="ar-pair">
        <h4>Turned down</h4>
        <ul className="ar-rejected">
          {node.rejected.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      </div>
      <div className="ar-pair">
        <h4>Sources</h4>
        <ul className="ar-sources">
          {node.sources.map((s) => (
            <li key={s}>
              <a href={`${REPO}/blob/main/${s}`}>
                <code>{s}</code>
              </a>
            </li>
          ))}
          {node.issues?.map((n) => (
            <li key={n}>
              <a href={`${REPO}/issues/${n}`}>Issue #{n}</a>
            </li>
          ))}
        </ul>
      </div>
    </motion.div>
  );
}

export function NodeButton({
  node,
  open,
  lit,
  current,
  onToggle,
}: {
  node: ArchNode;
  open: boolean;
  lit?: boolean;
  current?: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      className="ar-node"
      data-open={open || undefined}
      data-lit={lit || undefined}
      data-current={current || undefined}
      aria-expanded={open}
      aria-controls={`ar-d-${node.id}`}
      onClick={onToggle}
    >
      <span className="ar-node-title">{node.title}</span>
      <span className="ar-node-short">{node.short}</span>
      <StatusTag status={node.status} />
    </button>
  );
}
