import { GROUPS, KIND_TITLE, type Kind } from "@/lib/subscriptions";

export type Place = { kind: Kind; group: string };
export const placeValue = (p: Place) => `${p.kind}|${p.group}`;
const parse = (v: string): Place => {
  const [kind, ...rest] = v.split("|");
  return { kind: kind as Kind, group: rest.join("|") };
};

/** One native select over every Subscriptions and Bills group (native: best on a phone). */
export function PlacementSelect({
  label,
  value,
  onChange,
  disabled,
}: {
  label: string;
  value: Place;
  onChange: (p: Place) => void;
  disabled?: boolean;
}) {
  return (
    <select
      aria-label={label}
      value={placeValue(value)}
      disabled={disabled}
      onChange={(e) => onChange(parse(e.target.value))}
      className="min-h-9 max-w-full rounded-full bg-active px-3 text-label font-semibold disabled:opacity-50"
    >
      {(Object.keys(GROUPS) as Kind[]).map((kind) => (
        <optgroup key={kind} label={KIND_TITLE[kind]}>
          {GROUPS[kind].map((g) => (
            <option key={g} value={`${kind}|${g}`}>
              {g}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}
