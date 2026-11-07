import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useEffect, useRef } from "react";
import { fetchSyncStatus, postSync } from "@/api/client";
import { ago } from "@/components/BackupStatus";

const HOUR = 3_600_000;

/** Header control: when the bank last synced, a Sync now button, and a sync on open if that is over an hour ago (#77). */
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

  const run = () => {
    if (!busy) sync.mutate();
  };
  const note = sync.isError
    ? sync.error.message
    : sync.data
      ? `${sync.data.added} new`
      : last
        ? `Synced ${ago(last, now ?? new Date())}`
        : null;

  return (
    <div className="ml-auto flex items-center gap-3">
      {note && (
        <p
          role={sync.isError ? "status" : undefined}
          aria-live="polite"
          className={`max-w-28 text-xs leading-tight sm:max-w-64 sm:text-sm ${sync.isError ? "text-warning-foreground max-sm:absolute max-sm:top-full max-sm:right-3 max-sm:mt-2 max-sm:max-w-[calc(100vw-2rem)] max-sm:rounded-[10px] max-sm:bg-warning max-sm:px-3 max-sm:py-1.5" : sync.data ? "text-muted-foreground" : "hidden text-muted-foreground sm:block"}`}
        >
          {note}
        </p>
      )}
      <button
        type="button"
        aria-label={busy ? "Syncing" : "Sync now"}
        aria-disabled={busy}
        onClick={run}
        className="inline-flex min-h-11 min-w-11 items-center justify-center gap-2 rounded-full glass px-3 text-sm font-semibold transition-[background-color,transform] duration-150 ease-out active:scale-[0.97] sm:px-4 [@media(hover:hover)]:hover:bg-active"
      >
        <RefreshCw className={`size-4 ${busy ? "motion-safe:animate-spin" : ""}`} aria-hidden="true" />
        <span className="hidden sm:inline">{busy ? "Syncing" : "Sync now"}</span>
      </button>
    </div>
  );
}
