/** The only place money is formatted. Input is integer minor units + currency. */

export type Money = { minor: number; currency: string };
export type MoneyParts = { main: string; fraction: string };

const MINUS = "−";

export function formatMoneyParts(
  { minor, currency }: Money,
  opts: { sign?: boolean } = {},
): MoneyParts {
  const fmt = new Intl.NumberFormat("en-GB", { style: "currency", currency });
  // integer arithmetic on a BigInt string: no float ever touches the amount. The API's
  // minor is hundredths in every currency (#156); Intl then shows the currency's own digits
  const abs = BigInt(Math.abs(minor)).toString().padStart(3, "0");
  const decimal = `${abs.slice(0, -2)}.${abs.slice(-2)}`;
  const parts = fmt.formatToParts(decimal as unknown as number);
  const fraction = parts.find((p) => p.type === "fraction")?.value;
  const body = parts
    .filter((p) => p.type !== "fraction" && p.type !== "decimal")
    .map((p) => p.value)
    .join("");
  const sign = minor < 0 ? MINUS : opts.sign && minor > 0 ? "+" : "";
  return { main: sign + body, fraction: fraction ? `.${fraction}` : "" };
}

export function formatMoney(m: Money, opts: { sign?: boolean } = {}): string {
  const { main, fraction } = formatMoneyParts(m, opts);
  return main + fraction;
}
