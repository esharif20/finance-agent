import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { fetchNote, purgeNote, saveNote } from "@/api/client";

export const NOTE_MAX = 140;
const control = "min-h-11 rounded-full px-4 text-sm font-semibold transition-transform duration-150 ease-out active:scale-[0.97] disabled:opacity-50";

/**
 * A purpose note (up to 140 characters) on a Transaction, or on one part of its Split.
 * Editing keeps earlier versions; Delete purges every version, and says what backups do.
 */
export function NoteField({ txn, part = null, label = "Note" }: { txn: number; part?: number | null; label?: string }) {
  const client = useQueryClient();
  const id = useId();
  const key = ["note", txn, part] as const;
  const note = useQuery({ queryKey: key, queryFn: () => fetchNote(txn, part) });
  const saved = note.data?.text ?? "";
  const [text, setText] = useState("");
  const [confirming, setConfirming] = useState(false);
  useEffect(() => setText(saved), [saved]);

  const done = () => void client.invalidateQueries({ queryKey: ["note", txn] });
  const save = useMutation({ mutationFn: () => saveNote(txn, text.trim(), part), onSuccess: done });
  const purge = useMutation({
    mutationFn: () => purgeNote(txn, part),
    onSuccess: () => {
      setConfirming(false);
      done();
    },
  });
  const busy = save.isPending || purge.isPending;
  const clean = text.trim();
  const error = save.error ?? purge.error;

  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={id} className="text-label font-semibold">
        {label}
      </label>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start">
        <div className="min-w-0 flex-1">
          <input
            id={id}
            value={text}
            maxLength={NOTE_MAX}
            disabled={note.isPending || busy}
            onChange={(e) => {
              save.reset();
              setText(e.target.value);
            }}
            placeholder="What was this for?"
            aria-describedby={`${id}-count`}
            className="min-h-11 w-full rounded-full border border-[var(--control)] bg-card px-4 text-sm"
          />
          <p id={`${id}-count`} className="mt-1 px-4 text-meta text-muted-foreground">
            {text.length} of {NOTE_MAX}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => save.mutate()}
            disabled={busy || clean === "" || clean === saved}
            className={`${control} bg-primary text-primary-foreground`}
          >
            Save note
          </button>
          {note.data && !confirming && (
            <button type="button" onClick={() => setConfirming(true)} disabled={busy} className={`${control} bg-active`}>
              Delete
            </button>
          )}
        </div>
      </div>
      {confirming && (
        <div role="alertdialog" aria-label="Delete note" className="flex flex-col gap-2 rounded-[14px] bg-warning p-3 text-sm text-warning-foreground">
          <p>
            Delete this note and every earlier version of it? Backups keep a copy until they age out, about a month at
            most.
          </p>
          <div className="flex gap-2">
            <button type="button" onClick={() => purge.mutate()} disabled={busy} className={`${control} bg-primary text-primary-foreground`}>
              Delete every version
            </button>
            <button type="button" onClick={() => setConfirming(false)} className={`${control} bg-card text-foreground`}>
              Keep it
            </button>
          </div>
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-foreground">
          {error.message}
        </p>
      )}
    </div>
  );
}
