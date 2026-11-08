import { ChevronLeft, ChevronRight } from "lucide-react";
import { monthLabel } from "./MonthCard";

const step =
  "grid size-11 place-items-center rounded-full bg-raised text-foreground transition-colors enabled:hover:bg-active disabled:opacity-40";

/** `months` is newest first, as the API returns it. `onPreview`: a pointer or focus is on a
 * step, so its month can be fetched before the click. */
export function MonthSwitcher({
  months,
  month,
  onChange,
  onPreview,
}: {
  months: string[];
  month: string;
  onChange: (month: string) => void;
  onPreview?: (month: string) => void;
}) {
  const i = months.indexOf(month);
  const older = months[i + 1];
  const newer = i > 0 ? months[i - 1] : undefined;
  const ahead = (m: string | undefined) => () => {
    if (m) onPreview?.(m);
  };
  return (
    <nav aria-label="Month" className="flex items-center gap-2">
      <button
        type="button"
        className={step}
        aria-label="Previous month"
        disabled={!older}
        onClick={() => older && onChange(older)}
        onPointerEnter={ahead(older)}
        onFocus={ahead(older)}
      >
        <ChevronLeft className="size-5" aria-hidden="true" />
      </button>
      <span className="min-w-36 text-center text-row font-semibold tracking-[-0.01em]" aria-live="polite">
        {monthLabel(month)}
      </span>
      <button
        type="button"
        className={step}
        aria-label="Next month"
        disabled={!newer}
        onClick={() => newer && onChange(newer)}
        onPointerEnter={ahead(newer)}
        onFocus={ahead(newer)}
      >
        <ChevronRight className="size-5" aria-hidden="true" />
      </button>
    </nav>
  );
}
