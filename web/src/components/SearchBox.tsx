import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { useEffect, useId, useRef, useState } from "react";
import { fetchSearch } from "@/api/client";
import { iconButton } from "@/components/SyncNow";

const KIND: Record<string, string> = {
  transaction: "Transaction",
  merchant: "Merchant",
  note: "Note",
  split: "Split part",
  memory: "Memory",
};

/** Top-bar search (#279): an icon button that opens a small panel. Snippets are quoted data from the
 * user's own records, rendered as plain text; `/` opens it, Escape closes it. */
export function SearchBox() {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const id = useId();
  const reduced = useReducedMotion();

  useEffect(() => {
    const t = setTimeout(() => setQ(text.trim()), 250);
    return () => clearTimeout(t);
  }, [text]);

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement | null)?.closest("input,textarea,select,[contenteditable]");
      if (e.key === "/" && !typing && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        setOpen(true);
      } else if (e.key === "Escape" && open) {
        setOpen(false);
        button.current?.focus();
      }
    };
    const away = (e: PointerEvent) => {
      if (open && !box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", key);
    document.addEventListener("pointerdown", away);
    return () => {
      document.removeEventListener("keydown", key);
      document.removeEventListener("pointerdown", away);
    };
  }, [open]);

  const { data, isFetching, isError } = useQuery({
    queryKey: ["search", q],
    queryFn: () => fetchSearch(q),
    enabled: open && q.length > 0,
  });

  return (
    <div ref={box} className="relative">
      <button
        ref={button}
        type="button"
        aria-label="Search"
        aria-expanded={open}
        aria-controls={id}
        aria-keyshortcuts="/"
        data-tip="Search · /"
        data-tip-align="end"
        onClick={() => setOpen((o) => !o)}
        className={`${iconButton} ${open ? "bg-active" : ""}`}
      >
        <Search className="size-[18px]" aria-hidden="true" />
      </button>
      {open && (
        <motion.div
          id={id}
          role="search"
          data-float
          initial={reduced ? { opacity: 0 } : { opacity: 0, scale: 0.96, y: -4 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          transition={{ duration: reduced ? 0.1 : 0.16, ease: [0.23, 1, 0.32, 1] }}
          style={{ transformOrigin: "top right" }}
          className="absolute top-full right-0 z-30 mt-3 w-[min(22rem,calc(100vw-2rem))] rounded-[20px] border border-[var(--card-edge)] bg-card p-2 shadow-[var(--glass-shadow)]"
        >
          <input
            autoFocus
            type="search"
            value={text}
            maxLength={100}
            onChange={(e) => setText(e.target.value)}
            placeholder="Search your records"
            aria-label="Search your records"
            className="min-h-11 w-full rounded-[12px] bg-active px-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-primary"
          />
          <ul aria-live="polite" className="mt-1 max-h-72 overflow-auto">
            {data?.hits.map((h) => (
              <li key={`${h.kind}:${h.ref}:${h.snippet.data}`} className="flex flex-col gap-0.5 rounded-[12px] px-3 py-2">
                <span className="text-xs font-semibold text-muted-foreground">{KIND[h.kind] ?? h.kind}</span>
                <span className="text-sm break-words">{h.snippet.data}</span>
              </li>
            ))}
            {q && !isFetching && !isError && data?.hits.length === 0 && (
              <li className="px-3 py-2 text-sm text-muted-foreground">Nothing found.</li>
            )}
            {isError && <li className="px-3 py-2 text-sm text-muted-foreground">Search is unavailable.</li>}
          </ul>
        </motion.div>
      )}
    </div>
  );
}
