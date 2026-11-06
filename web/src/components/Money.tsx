import { useCountUp } from "@/lib/countup";
import { type Money as MoneyValue, formatMoney, formatMoneyParts } from "@/lib/money";
import { usePrivacy } from "@/lib/privacy";
import { cn } from "@/lib/utils";

/**
 * Every amount on screen goes through here. Pence are set a step smaller.
 * In privacy mode the CSS blurs `.money` and screen readers hear "Amount hidden".
 */
export function Money({
  value,
  sign,
  countUp,
  className,
}: {
  value: MoneyValue;
  sign?: boolean;
  countUp?: boolean;
  className?: string;
}) {
  const { hidden } = usePrivacy();
  const minor = useCountUp(value.minor, countUp === true);
  const { main, fraction } = formatMoneyParts({ ...value, minor }, { sign });
  return (
    <span className={cn("money whitespace-nowrap", className)}>
      <span className="sr-only">{hidden ? "Amount hidden" : formatMoney(value, { sign })}</span>
      <span aria-hidden="true">
        {main}
        <span className="text-[0.62em] font-medium text-muted-foreground">{fraction}</span>
      </span>
    </span>
  );
}
