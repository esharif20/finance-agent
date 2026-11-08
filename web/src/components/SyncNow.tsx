import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useEffect, useRef } from "react";
import { fetchSyncStatus, postSync } from "@/api/client";
import { ago } from "@/components/BackupStatus";

const HOUR = 3_600_000;

/** Round icon control in the top bar: hover fill on fine pointers, a small press. */
export const iconButton =
  "relative inline-flex size-11 shrink-0 items-center justify-center rounded-full transition-[background-color,scale] duration-150 ease-out active:scale-[0.94] [@media(hover:hover)]:hover:bg-active";

/** Header control: one icon button whose name and tooltip say when the bank last synced, a
 * sync on open if that is over an hour ago (#77), and the result in a small note under it (#214). */
export function SyncNow({ now }: { now?: Date }) {
  const client = useQueryClient();
  const { data } = useQuery({ queryKey: ["sync-status"], queryFn: fetchSyncStatus });
  const sync = useMutation({
    mutationFn: postSync,
    // like an import: everything on screen may have changed
    onSettled: () => client.invalidateQueries(),
  });
  const busy = sync.isPending;
  const asked = useRef(false); // one automatic sync per page load, never alongside another

  const last = data?.last_sync_at ? new Date(data.last_sync_at) : null;
  useEffect(() => {
    if (asked.current || !last || busy) return;
    asked.current = true;
    if ((now ?? new Date()).getTime() - last.getTime() > HOUR) sync.mutate();
  }, [last, busy, now, sync]);

  // the count is news for a moment; an error stays until the next try
  const { reset } = sync;
  useEffect(() => {
    if (!sync.data) return;
    const t = setTimeout(reset, 5000);
    return () => clearTimeout(t);
  }, [sync.data, reset]);

  const run = () => {
    if (!busy) sync.mutate();
  };
  const label = busy ? "Syncing" : last ? `Last synced ${ago(last, now ?? new Date())} · Sync now` : "Sync now";
  const note = sync.isError ? sync.error.message : sync.data ? `${sync.data.added} new` : null;

  return (
    <div className="relative">
      <button
        type="button"
        aria-label={label}
        aria-disabled={busy}
        data-tip={label}
        data-tip-align="end"
        onClick={run}
        className={iconButton}
      >
        <RefreshCw className={`size-[18px] ${busy ? "motion-safe:animate-spin" : ""}`} aria-hidden="true" />
        {sync.isError && <span aria-hidden="true" className="absolute top-2.5 right-2.5 size-2 rounded-full bg-warning-foreground" />}
      </button>
      <p
        role={sync.isError ? "status" : undefined}
        aria-live="polite"
        data-float
        className={
          note
            ? `absolute top-full right-0 mt-3 w-max max-w-[min(18rem,calc(100vw-2rem))] rounded-[12px] px-3 py-1.5 text-sm font-semibold whitespace-normal ${sync.isError ? "bg-warning text-warning-foreground" : "bg-foreground text-background"}`
            : "sr-only"
        }
      >
        {note}
      </p>
    </div>
  );
}
