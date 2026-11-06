import { type Money as MoneyValue, formatMoney, formatMoneyParts } from "@/lib/money";
import { cn } from "@/lib/utils";

/** Every amount on screen goes through here. Pence are set a step smaller. */
export function Money({
  value,
  sign,
  className,
}: {
  value: MoneyValue;
  sign?: boolean;
  className?: string;
}) {
  const { main, fraction } = formatMoneyParts(value, { sign });
  return (
    <span className={cn("money whitespace-nowrap", className)}>
      <span className="sr-only">{formatMoney(value, { sign })}</span>
      <span aria-hidden="true">
        {main}
        <span className="text-[0.62em] font-medium text-muted-foreground">
          {fraction}
        </span>
      </span>
    </span>
  );
}
