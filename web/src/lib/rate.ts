// Mirrors the API's RateIn (#181): ASCII digits only, up to 7 whole and 12 decimal places, above zero.
// The API is the authority; this refuses the same input before it is sent.
export function checkRate(raw: string): string | null {
  const v = raw.trim();
  if (!v) return "Enter the rate in pounds per unit.";
  if (/^[0-9]+\.[0-9]{13,}$/.test(v)) return "Use at most 12 decimal places.";
  if (!/^[0-9]{1,7}(\.[0-9]{1,12})?$/.test(v)) return "Use digits and one dot only, like 0.85.";
  if (!/[1-9]/.test(v)) return "The rate must be more than zero.";
  return null;
}
