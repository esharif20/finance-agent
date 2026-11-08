import { RotateCcw, Shuffle } from "lucide-react";
import { useState } from "react";

const button =
  "inline-flex min-h-9 items-center gap-1.5 rounded-full bg-raised px-3.5 text-sm font-semibold transition-[background-color,scale] duration-150 ease-out active:scale-[0.97] disabled:opacity-50 [@media(hover:hover)]:hover:bg-active";

type Action = "reset" | "shuffle";

/** The demo's persistent strip: says the data is synthetic, and puts the visitor's ledger back
 * (Reset) or deals them another Persona (Shuffle). Either reloads the page. */
export function DemoBanner() {
  const [busy, setBusy] = useState<Action | null>(null);
  const [failed, setFailed] = useState(false);

  async function run(action: Action) {
    setBusy(action);
    setFailed(false);
    try {
      const res = await fetch(`/api/demo/${action}`, { method: "POST" });
      if (!res.ok) throw new Error(String(res.status));
      // a fresh page, not a refetch: every screen, draft and conversation starts over on the new ledger
      window.location.reload();
    } catch {
      setFailed(true);
      setBusy(null);
    }
  }

  return (
    <aside
      aria-label="Demo"
      className="relative z-30 border-b border-border bg-card text-card-foreground"
    >
      <div className="mx-auto flex w-full max-w-[1120px] flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-2 sm:px-9">
        <p className="text-sm font-semibold">
          Synthetic data. Nothing here is real.
          {failed && (
            <span role="alert" className="ml-2 font-normal text-muted-foreground">
              That didn't work. Try again.
            </span>
          )}
        </p>
        <div className="flex gap-2">
          <button type="button" className={button} disabled={busy !== null} onClick={() => run("reset")}>
            <RotateCcw className="size-4" aria-hidden="true" />
            Reset
          </button>
          <button type="button" className={button} disabled={busy !== null} onClick={() => run("shuffle")}>
            <Shuffle className="size-4" aria-hidden="true" />
            Shuffle
          </button>
        </div>
      </div>
    </aside>
  );
}
