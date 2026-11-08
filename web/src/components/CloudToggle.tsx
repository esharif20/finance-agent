import { Cloud } from "lucide-react";

/** Per-conversation opt-in to the privacy gateway (#304). Off by default; the parent resets it with a new thread. */
export function CloudToggle({ on, capped, disabled, onChange }: { on: boolean; capped: boolean; disabled?: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border px-4 py-2.5 sm:px-6">
      <label className={`inline-flex min-h-11 items-center gap-2 text-label font-semibold ${capped ? "opacity-60" : ""}`}>
        <input
          type="checkbox"
          role="switch"
          checked={on && !capped}
          disabled={capped || disabled}
          onChange={(e) => onChange(e.target.checked)}
          className="size-5 accent-primary"
        />
        <Cloud className="size-4" aria-hidden="true" />
        Use the cloud model for this conversation
      </label>
      <p className="text-meta text-muted-foreground">
        {capped
          ? "This month's cloud budget is used up, so answers stay on this Mac."
          : "Only placeholders for names and categories are sent, never amounts or descriptions."}
      </p>
    </div>
  );
}
