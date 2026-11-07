import { useEffect, useRef } from "react";

/** A number that re-enters (CSS .count-tick) whenever it changes, but not on first render. */
export function CountTick({ value }: { value: number }) {
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
  }, []);
  return (
    <span
      key={value}
      className={`inline-block tabular-nums ${mounted.current ? "count-tick" : ""}`}
      data-tick={mounted.current ? "" : undefined}
    >
      {value}
    </span>
  );
}
