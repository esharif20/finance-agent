import type { components } from "./schema";

export type RateOut = components["schemas"]["RateOut"];

/** Enter a rate (#187). `rate` is a Decimal string, never a number; a refusal throws the API's own reason. */
export async function putRate(currency: string, on: string, rate: string): Promise<RateOut> {
  const res = await fetch("/api/fx/rates", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ currency, on, rate }),
  });
  if (!res.ok) {
    const j: unknown = await res.json().catch(() => null);
    const detail = (j as { detail?: unknown } | null)?.detail;
    throw new Error(typeof detail === "string" ? detail : "Couldn't save that rate. Check it and try again.");
  }
  return (await res.json()) as RateOut;
}
