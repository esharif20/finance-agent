import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { answerPair, fetchNextPair } from "@/api/client";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";

const KEY = ["next-pair"] as const;
const PER_VISIT = 3; // "at most a few a week": the briefing is a weekly page

const pick =
  "inline-flex min-h-11 flex-1 items-center justify-center rounded-full bg-active px-4 text-sm font-semibold transition-[background-color,transform] duration-150 ease-out active:scale-[0.97] disabled:opacity-50 motion-reduce:transition-none motion-reduce:active:scale-100";

/** One "which would you cut first?" pair (#268). Choosing posts the answer and asks for the next
 * pair; Skip records nothing and just hides the card. */
export function PairwiseCard() {
  const client = useQueryClient();
  const [done, setDone] = useState(0);
  const [skipped, setSkipped] = useState(false);
  const { data: pair } = useQuery({ queryKey: KEY, queryFn: fetchNextPair, refetchOnWindowFocus: false });
  const save = useMutation({
    mutationFn: (choice: string) => answerPair(pair?.pair_id ?? 0, choice),
    onSuccess: () => {
      setDone((n) => n + 1);
      return client.invalidateQueries({ queryKey: KEY });
    },
  });
  if (skipped || done >= PER_VISIT || !pair || typeof pair.pair_id !== "number") return null;
  return (
    <Card aria-labelledby="brief-pair">
      <CardHeader>
        <CardTitle id="brief-pair">{pair.prompt}</CardTitle>
      </CardHeader>
      <div className="flex flex-col gap-2 sm:flex-row" role="group" aria-label={pair.prompt}>
        {[pair.a, pair.b].map((item) => (
          <button key={item.key} type="button" disabled={save.isPending} onClick={() => save.mutate(item.key)} className={pick}>
            {item.label}
          </button>
        ))}
      </div>
      {save.error && (
        <p role="alert" className="mt-2 text-label text-warning-foreground">
          Couldn't save that. Try again.
        </p>
      )}
      <div className="mt-2 flex items-center justify-between gap-3">
        <p className="text-label text-muted-foreground">Your picks tune what I flag. No money moves.</p>
        <button
          type="button"
          onClick={() => setSkipped(true)}
          className="inline-flex min-h-11 items-center px-2 text-sm font-semibold underline-offset-4 hover:underline"
        >
          Skip
        </button>
      </div>
    </Card>
  );
}
