import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Plus, X } from "lucide-react";
import { useState } from "react";
import { type CategoryGroup, saveSplit } from "@/api/client";
import { Money } from "@/components/Money";
import { MAX_PARTS, MIN_PARTS, splitState } from "@/lib/split";

type Row = { key: number; amount: string; category: string }; // category: "group.sub" index pair, "" unset
type Part = { amount_minor: number; category: string[] };

const control = "min-h-11 rounded-full px-4 text-sm font-semibold transition-transform duration-150 ease-out active:scale-[0.97] disabled:opacity-50";
const field = "min-h-11 rounded-full border border-[var(--control)] bg-card px-4 text-sm";

const pounds = (minor: number) => {
  const a = Math.abs(minor);
  return `${Math.floor(a / 100)}.${String(a % 100).padStart(2, "0")}`;
};

/**
 * Split one payment into 2 to 10 parts. You type every amount except the last, which is the
 * remainder, shown live. Save stays disabled until the parts add up to the payment to the penny.
 */
export function SplitEditor({
  txn,
  total,
  currency,
  groups,
  initial,
  onDone,
}: {
  txn: number;
  /** The payment's signed minor units. */
  total: number;
  currency: string;
  groups: CategoryGroup[];
  /** The parts of an existing Split, to re-split from. */
  initial?: Part[];
  onDone: () => void;
}) {
  const client = useQueryClient();
  const options = groups.map((g, gi) => ({ ...g, gi })).filter((g) => g.name !== "Uncategorised" && g.name !== "To review");
  const indexOf = (cat: string[]) => {
    const g = groups.findIndex((x) => x.name === cat[0]);
    const s = groups[g]?.subs.indexOf(cat[1] ?? "") ?? -1;
    return g >= 0 && s >= 0 ? `${g}.${s}` : "";
  };
  const [n, setN] = useState(2);
  const [rows, setRows] = useState<Row[]>(() =>
    initial && initial.length >= MIN_PARTS
      ? initial.map((p, i) => ({ key: i, amount: pounds(p.amount_minor), category: indexOf(p.category) }))
      : [0, 1].map((key) => ({ key, amount: "", category: "" })),
  );
  const state = splitState(total, rows);
  const ready = state.valid && rows.every((r) => r.category !== "");
  const patch = (i: number, p: Partial<Row>) => setRows((rs) => rs.map((r, k) => (k === i ? { ...r, ...p } : r)));

  const save = useMutation({
    mutationFn: () =>
      saveSplit(
        txn,
        rows.map((r, i) => {
          const [g, s] = r.category.split(".").map(Number);
          const category = [groups[g ?? -1]?.name ?? "", groups[g ?? -1]?.subs[s ?? -1] ?? ""];
          // the last part leaves its amount out: the Ledger fills in the remainder
          return i === rows.length - 1 ? { category } : { category, amount_minor: state.minors[i] };
        }),
      ),
    onSuccess: () => {
      onDone();
      return client.invalidateQueries();
    },
  });

  const last = rows.length - 1;
  const over = state.remainder !== 0 && Math.sign(state.remainder) !== Math.sign(total);
  return (
    <form
      aria-label="Split payment"
      onSubmit={(e) => {
        e.preventDefault();
        if (ready) save.mutate();
      }}
      className="flex flex-col gap-3"
    >
      <ol className="flex flex-col gap-2">
        {rows.map((r, i) => (
          <li key={r.key} className="flex flex-wrap items-center gap-2">
            {i === last ? (
              <div
                aria-label={`Part ${i + 1} amount, the remainder`}
                role="group"
                className={`${field} flex w-28 items-center justify-end border-dashed bg-raised ${over || !state.valid ? "text-muted-foreground" : "font-semibold"}`}
              >
                {over ? "Over" : <Money value={{ minor: Math.abs(state.remainder), currency }} />}
              </div>
            ) : (
              <span className="relative">
                <span aria-hidden="true" className="pointer-events-none absolute top-1/2 left-4 -translate-y-1/2 text-muted-foreground">
                  £
                </span>
                <input
                  aria-label={`Part ${i + 1} amount in pounds`}
                  inputMode="decimal"
                  autoComplete="off"
                  value={r.amount}
                  onChange={(e) => patch(i, { amount: e.target.value })}
                  placeholder="0.00"
                  className={`${field} money w-28 pl-8 text-right tabular-nums`}
                />
              </span>
            )}
            <label className="relative min-w-0 flex-1 basis-44">
              <span className="sr-only">Part {i + 1} category</span>
              <select
                value={r.category}
                onChange={(e) => patch(i, { category: e.target.value })}
                className={`${field} w-full appearance-none pr-10`}
              >
                <option value="">Choose a category…</option>
                {options.map((g) => (
                  <optgroup key={g.name} label={g.name}>
                    {g.subs.map((s) => (
                      <option key={s} value={`${g.gi}.${g.subs.indexOf(s)}`}>
                        {s}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
              <ChevronDown className="pointer-events-none absolute top-1/2 right-4 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
            </label>
            {rows.length > MIN_PARTS && (
              <button
                type="button"
                aria-label={`Remove part ${i + 1}`}
                onClick={() => setRows((rs) => rs.filter((_, k) => k !== i))}
                className={`${control} inline-flex min-w-11 items-center justify-center px-0 [@media(hover:hover)]:hover:bg-active`}
              >
                <X className="size-4" aria-hidden="true" />
              </button>
            )}
          </li>
        ))}
      </ol>

      <p role="status" className="min-h-5 text-label text-muted-foreground">
        {over ? (
          <>
            Parts are over by <Money value={{ minor: Math.abs(state.remainder), currency }} />. Lower an amount.
          </>
        ) : state.valid ? (
          <>
            The last part is the remainder, <Money value={{ minor: Math.abs(state.remainder), currency }} />. Nothing is saved until you press Save split.
          </>
        ) : (
          "Type an amount for every part but the last; it takes what is left."
        )}
      </p>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={rows.length >= MAX_PARTS}
          onClick={() => {
            setRows((rs) => [...rs, { key: 100 + n, amount: "", category: "" }]);
            setN((k) => k + 1);
          }}
          className={`${control} inline-flex items-center gap-2 bg-active`}
        >
          <Plus className="size-4" aria-hidden="true" />
          Add part
        </button>
        <button type="submit" disabled={!ready || save.isPending} className={`${control} bg-primary text-primary-foreground`}>
          Save split
        </button>
        <button type="button" onClick={onDone} className={`${control} [@media(hover:hover)]:hover:bg-active`}>
          Cancel
        </button>
      </div>
      {save.error && (
        <p role="alert" className="text-sm">
          {save.error.message}
        </p>
      )}
    </form>
  );
}
