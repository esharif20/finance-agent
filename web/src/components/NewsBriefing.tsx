import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { type NewsCard, type NewsTopic, fetchNews, forgetNewsTopic, markNewsRead, setNewsTopic } from "@/api/client";
import { Money } from "@/components/Money";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export const NEWS_KEY = ["briefing", "news"] as const;

const day = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });

const pill =
  "inline-flex min-h-11 items-center rounded-full px-4 text-sm font-semibold transition-[background-color,transform] duration-150 ease-out active:scale-[0.97] [@media(hover:hover)]:hover:bg-active disabled:opacity-50";

const CHANGE: Record<string, string> = {
  price_change: "Price change",
  plan_change: "Plan change",
  outage: "Outage",
  release: "Release",
  switching: "People switching",
  other: "News",
};

function useNews() {
  const q = useQuery({ queryKey: NEWS_KEY, queryFn: fetchNews });
  // an older server, or the demo, has no news: show nothing rather than an error
  return q.data && Array.isArray(q.data.topics) ? q.data : undefined;
}

function TopicCard({ card, months, n }: { card: NewsCard; months: number; n: number }) {
  const client = useQueryClient();
  const done = () => client.invalidateQueries({ queryKey: NEWS_KEY });
  const read = useMutation({ mutationFn: markNewsRead, onSettled: done });
  const mute = useMutation({ mutationFn: () => setNewsTopic(card.topic, "off"), onSettled: done });
  return (
    <Card aria-labelledby={`news-${n}`} className="flex flex-col gap-4">
      <CardHeader className="mb-0 flex-wrap gap-2">
        <CardTitle id={`news-${n}`}>{card.topic}</CardTitle>
        <button type="button" className={cn(pill, "-mr-2")} disabled={mute.isPending} onClick={() => mute.mutate()}>
          Mute topic
        </button>
      </CardHeader>
      {card.spent && (
        <p className="text-label text-muted-foreground">
          Relevant to your spending: <Money value={card.spent} /> in the last {months} months
        </p>
      )}
      <ul className="divide-y divide-border">
        {card.items.map((i) => (
          <li key={i.id} className={cn("py-4 first:pt-0 last:pb-0", i.read && "text-muted-foreground")}>
            <p className="text-label text-muted-foreground">
              {CHANGE[i.change] ?? "News"}, {day(i.on)}
            </p>
            <p className="font-semibold">{i.headline}</p>
            <p className="mt-1 max-w-prose text-sm">{i.summary}</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {i.sources.map((s) => (
                <a
                  key={s.url}
                  href={s.url}
                  target="_blank"
                  rel="noopener noreferrer nofollow"
                  className="inline-flex min-h-11 items-center rounded-full border border-border px-3 text-label underline-offset-4 hover:underline"
                >
                  {s.title}
                </a>
              ))}
              {!i.read && (
                <button type="button" className={pill} disabled={read.isPending} onClick={() => read.mutate(i.id)}>
                  Mark read
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** News on the topics you ticked, a card per topic (#309). Web text is shown as plain text with its source. */
export function NewsCards() {
  const news = useNews();
  if (!news?.available) return null;
  const ticked = news.topics.some((t) => t.state === "on");
  return (
    <section aria-labelledby="news-title" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4">
        <h2 id="news-title" className="text-heading">
          News on your topics
        </h2>
        <a href="#settings" className="inline-flex min-h-11 items-center text-sm font-semibold underline-offset-4 hover:underline">
          Choose topics
        </a>
      </div>
      {news.cards.length ? (
        <div className="grid items-start gap-6 lg:grid-cols-2">
          {news.cards.map((c, n) => (
            <TopicCard key={c.topic} card={c} months={news.window_months} n={n} />
          ))}
        </div>
      ) : (
        <p className="text-muted-foreground">
          {ticked
            ? "Nothing new on your topics yet. The briefing updates once a day."
            : "Tick a few topics in Settings and the daily briefing will gather news on them."}
        </p>
      )}
    </section>
  );
}

function TopicRow({ topic, months }: { topic: NewsTopic; months: number }) {
  const client = useQueryClient();
  const done = () => client.invalidateQueries({ queryKey: NEWS_KEY });
  const set = useMutation({ mutationFn: (s: "on" | "off") => setNewsTopic(topic.name, s), onSettled: done });
  const forget = useMutation({ mutationFn: () => forgetNewsTopic(topic.name), onSettled: done });
  const state = set.isPending ? set.variables : topic.state;
  return (
    <li className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 py-3 first:pt-0 last:pb-0">
      <div className="min-w-0">
        <p className="font-semibold">{topic.name}</p>
        <p className="text-label text-muted-foreground">
          {topic.spent ? (
            <>
              From your spending: <Money value={topic.spent} /> in {months} months
            </>
          ) : (
            "Added by you"
          )}
        </p>
      </div>
      <div className="-mx-2 flex items-center gap-1" role="group" aria-label={`Send ${topic.name}?`}>
        <button type="button" aria-pressed={state === "on"} className={cn(pill, state === "on" && "bg-active")} onClick={() => set.mutate("on")}>
          Include
        </button>
        <button type="button" aria-pressed={state === "off"} className={cn(pill, state === "off" && "bg-active")} onClick={() => set.mutate("off")}>
          Block
        </button>
        {!topic.from_spending && (
          <button type="button" className={pill} onClick={() => forget.mutate()}>
            Remove
          </button>
        )}
      </div>
    </li>
  );
}

/** Settings: which topics the daily briefing may send. Only ticked names leave, mixed with decoys. */
export function NewsTopics() {
  const news = useNews();
  const client = useQueryClient();
  const [name, setName] = useState("");
  const add = useMutation({
    mutationFn: (n: string) => setNewsTopic(n, "on"),
    onSuccess: () => setName(""),
    onSettled: () => client.invalidateQueries({ queryKey: NEWS_KEY }),
  });
  if (!news?.available) return null;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (name.trim()) add.mutate(name.trim());
  };
  return (
    <Card aria-labelledby="news-topics-title" className="flex flex-col gap-4">
      <CardHeader className="mb-0">
        <CardTitle id="news-topics-title">Briefing topics</CardTitle>
      </CardHeader>
      <p className="text-sm text-muted-foreground">
        Once a day, the names you include are sent to Gemini with a few unrelated topics mixed in, to find news. Nothing
        else leaves this Mac: no amounts, accounts or shop names.
      </p>
      {news.topics.length ? (
        <ul className="divide-y divide-border">
          {news.topics.map((t) => (
            <TopicRow key={t.name} topic={t} months={news.window_months} />
          ))}
        </ul>
      ) : (
        <p className="text-muted-foreground">No topics from your spending yet. Add one below.</p>
      )}
      <form onSubmit={submit} className="flex flex-wrap items-center gap-2">
        <label htmlFor="news-add" className="sr-only">
          Add a topic
        </label>
        <input
          id="news-add"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Add a topic, e.g. Electric cars"
          maxLength={40}
          className="min-h-11 min-w-0 flex-1 rounded-full border border-border bg-transparent px-4 text-sm"
        />
        <button type="submit" className={cn(pill, "bg-active")} disabled={add.isPending}>
          Add
        </button>
      </form>
      {add.error && (
        <p role="alert" className="text-label text-warning-foreground">
          {add.error.message}
        </p>
      )}
    </Card>
  );
}
