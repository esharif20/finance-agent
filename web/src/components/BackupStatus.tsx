import { useQuery } from "@tanstack/react-query";
import { HardDrive, TriangleAlert } from "lucide-react";
import { fetchSyncStatus } from "@/api/client";

const DAY = 86_400_000;
const rtf = new Intl.RelativeTimeFormat("en-GB", { numeric: "auto" });

function ago(at: Date, now: Date): string {
  const ms = now.getTime() - at.getTime();
  if (ms >= DAY) return rtf.format(-Math.floor(ms / DAY), "day");
  if (ms >= 3_600_000) return rtf.format(-Math.floor(ms / 3_600_000), "hour");
  return "just now";
}

/** When the last backup ran (#95); a warning when a registered drive has no copy in 7 days. */
export function BackupStatus({ now }: { now?: Date }) {
  const { data } = useQuery({ queryKey: ["sync-status"], queryFn: fetchSyncStatus });
  if (!data || (!data.last_backup_at && !data.backup_warning)) return null;
  const when = data.last_backup_at ? ago(new Date(data.last_backup_at), now ?? new Date()) : null;
  if (data.backup_warning) {
    return (
      <p
        role="status"
        className="inline-flex items-center gap-2 rounded-[10px] bg-warning px-3 py-1.5 text-sm text-warning-foreground"
      >
        <TriangleAlert className="size-4 shrink-0" aria-hidden="true" />
        {when ? `Last backup ${when}.` : "No backup yet."} Connect your backup drive.
      </p>
    );
  }
  return (
    <p className="inline-flex items-center gap-2 text-sm text-muted-foreground">
      <HardDrive className="size-4 shrink-0" aria-hidden="true" />
      Backed up {when}
    </p>
  );
}
