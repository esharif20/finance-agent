import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  fetchBriefing,
  fetchKnowYou,
  fetchLearned,
  fetchMemories,
  forgetMemory,
  resetBandit,
  resetLifeChanges,
  resetMap,
  resetPreferences,
  resetAssistantFeedback,
  resetInsightFeedback,
  resetRegretAnswers,
} from "@/api/client";
import { INSIGHTS_KEY, Explained } from "@/components/InsightsFeed";
import { SpendMeter } from "@/components/SpendMeter";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { BRIEFING_KEY } from "@/screens/Briefing";

// the demo has no cloud and no spend store
const DEMO = import.meta.env.VITE_DEMO === "1";
const MEMORIES_KEY = ["memories"] as const;
const KNOW_KEY = ["know-you"] as const;
const LEARNED_KEY = ["preferences", "cut"] as const;
const SERIES: Record<string, string> = { income: "Income", rent: "Rent", bills: "Bills", groceries: "Groceries" };
const monthName = (m: string) =>
  new Date(`${m}-01T00:00:00Z`).toLocaleDateString("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });

const btn =
  "inline-flex min-h-11 items-center rounded-full px-4 text-sm font-semibold transition-[background-color,transform] duration-150 ease-out active:scale-[0.97] [@media(hover:hover)]:hover:bg-active disabled:opacity-50";

/** A reset asks once more before it wipes, so a stray tap never forgets anything. */
function Reset({ label, what, run }: { label: string; what: string; run: () => Promise<void> }) {
  const [sure, setSure] = useState(false);
  const m = useMutation({ mutationFn: run, onSuccess: () => setSure(false) });
  return (
    <div className="flex flex-wrap items-center gap-1">
      {sure ? (
        <>
          <button type="button" disabled={m.isPending} onClick={() => m.mutate()} className={`${btn} bg-active`}>
            Yes, {label.toLowerCase()}
          </button>
          <button type="button" onClick={() => setSure(false)} className={btn}>
            Keep it
          </button>
        </>
      ) : (
        <button type="button" aria-label={`${label}: ${what}`} onClick={() => setSure(true)} className={btn}>
          {label}
        </button>
      )}
      {m.error && (
        <p role="alert" className="text-label text-warning-foreground">
          Couldn't reset that. Try again.
        </p>
      )}
    </div>
  );
}

function Item({ title, body, children }: { title: string; body: string; children: React.ReactNode }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 py-4 first:pt-0 last:pb-0">
      <div className="min-w-0 max-w-prose">
        <p className="font-semibold">{title}</p>
        <p className="text-sm text-muted-foreground">{body}</p>
      </div>
      {children}
    </li>
  );
}

/** What the app has learned about you, in plain words, each with a reset that wipes it (#154). */
export function KnowYou() {
  const client = useQueryClient();
  const refresh = () => Promise.all([INSIGHTS_KEY, BRIEFING_KEY, MEMORIES_KEY, KNOW_KEY, LEARNED_KEY].map((k) => client.invalidateQueries({ queryKey: k })));
  const reset = (fn: () => Promise<void>) => async () => {
    await fn();
    await refresh();
  };
  const memories = useQuery({ queryKey: MEMORIES_KEY, queryFn: fetchMemories });
  const briefing = useQuery({ queryKey: BRIEFING_KEY, queryFn: fetchBriefing });
  const forget = useMutation({
    mutationFn: forgetMemory,
    onSettled: () => void client.invalidateQueries({ queryKey: MEMORIES_KEY }),
  });
  const know = useQuery({ queryKey: KNOW_KEY, queryFn: fetchKnowYou });
  const learned = useQuery({ queryKey: LEARNED_KEY, queryFn: fetchLearned });
  const strengths = learned.data?.items ?? [];
  const lifeChanges = know.data?.life_changes ?? [];
  const b = know.data?.bandit;
  const edges = know.data?.map_edges ?? 0;
  const answered = briefing.data?.regret.filter((r) => r.worth_it !== null).length;
  const gaps = briefing.data?.insufficient ?? [];
  return (
    <div className="flex flex-col gap-6">
      <p className="max-w-prose text-muted-foreground">
        Everything I have learned from you lives here, and none of it leaves this device. Reset any part and I start
        that part again from scratch.
      </p>
      <Card aria-labelledby="know-signals">
        <CardHeader>
          <CardTitle id="know-signals">What I have picked up</CardTitle>
        </CardHeader>
        <ul className="divide-y divide-border">
          <Item
            title="Which insights are useful"
            body="Your thumbs up, thumbs down and dismissals decide which kinds of insight I show first."
          >
            <Reset label="Reset" what="insight ratings" run={reset(resetInsightFeedback)} />
          </Item>
          <Item
            title="What was worth it"
            body={
              answered === undefined
                ? "Your answers to the weekly “was it worth it?” review."
                : `You have answered ${answered} this week. Your answers to the weekly review tune what I flag.`
            }
          >
            <Reset label="Reset" what="worth-it answers" run={reset(resetRegretAnswers)} />
          </Item>
          <Item
            title="Which assistant suggestions you accept"
            body="Which proposals you confirmed or turned down, so I stop suggesting what you keep declining."
          >
            <Reset label="Reset" what="assistant feedback" run={reset(resetAssistantFeedback)} />
          </Item>
        </ul>
      </Card>
      <Card aria-labelledby="know-deeper">
        <CardHeader>
          <CardTitle id="know-deeper">What I have worked out</CardTitle>
        </CardHeader>
        <ul className="divide-y divide-border">
          <Item
            title="What you would cut first"
            body={
              strengths.length
                ? `From ${learned.data?.answers} answers: you'd keep ${strengths.at(0)?.label} longest and cut ${strengths.at(-1)?.label} first.`
                : "No answers yet. Answer a “which would you cut first?” and it shows here."
            }
          >
            {strengths.length > 0 && <Reset label="Reset" what="cut-first answers" run={reset(resetPreferences)} />}
          </Item>
          <Item
            title="How my shadow ranker compares"
            body={
              b && b.rated > 0 && b.live_pct !== null && b.shadow_pct !== null
                ? `On ${b.rated} insights you rated, the ranker you see scored ${b.live_pct}% and my shadow ranker would have scored ${b.shadow_pct}%. It only watches: it never changes what you see.`
                : "Not enough rated insights yet to compare. Nothing changes what you see until you decide."
            }
          >
            {b && b.rated > 0 && <Reset label="Reset" what="shadow ranker" run={reset(resetBandit)} />}
          </Item>
          <Item
            title="Life changes you confirmed"
            body={
              lifeChanges.length
                ? lifeChanges.map((c) => `${SERIES[c.series] ?? c.series} changed in ${monthName(c.month)}`).join(". ") + "."
                : "None yet. I only note a change when you confirm it."
            }
          >
            {lifeChanges.length > 0 && <Reset label="Reset" what="life changes" run={reset(resetLifeChanges)} />}
          </Item>
          <Item
            title="Connections you confirmed"
            body={
              edges
                ? `${edges} connection${edges === 1 ? "" : "s"} between your merchants, categories and goals.`
                : "None yet. I only join two things when you confirm it."
            }
          >
            {edges > 0 && <Reset label="Reset" what="connections" run={reset(resetMap)} />}
          </Item>
        </ul>
      </Card>
      <Card aria-labelledby="know-memories">
        <CardHeader>
          <CardTitle id="know-memories">What you have told me</CardTitle>
        </CardHeader>
        {memories.data?.length ? (
          <ul className="divide-y divide-border">
            {memories.data.map((m) => (
              <Item key={m.id} title={m.tags[0] ?? "Note"} body="">
                <p className="order-first w-full text-sm">
                  <Explained text={m.text} />
                </p>
                <button
                  type="button"
                  disabled={forget.isPending}
                  aria-label={`Forget: ${m.text.slice(0, 40)}`}
                  onClick={() => forget.mutate(m.id)}
                  className={btn}
                >
                  Forget
                </button>
              </Item>
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground">
            {memories.isError ? "Couldn't load your memories. Try again in a moment." : "Nothing yet. I only remember what you ask me to, and you confirm each one."}
          </p>
        )}
        {forget.error && (
          <p role="alert" className="mt-3 text-label text-warning-foreground">
            Couldn't forget that. Try again.
          </p>
        )}
      </Card>
      {!DEMO && <SpendMeter />}
      <Card aria-labelledby="know-ready">
        <CardHeader>
          <CardTitle id="know-ready">What I can't tell yet</CardTitle>
        </CardHeader>
        {gaps.length ? (
          <ul className="list-disc pl-5 text-sm">
            {gaps.includes("insights") && <li>Insights: not enough history to compare against your own normal.</li>}
            {gaps.includes("forecast") && <li>Forecast: not enough history in any category to project spending.</li>}
          </ul>
        ) : (
          <p className="text-muted-foreground">I have enough history for insights and the forecast.</p>
        )}
      </Card>
    </div>
  );
}
