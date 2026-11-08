import { describe, expect, it } from "vitest";
import { type Chat, friendlyLabel, initial, parseBlocks, parseSse, pathLabel, reduce } from "./chat";

// synthetic
const ev = (name: string, data: object) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;

describe("parseSse", () => {
  it("reads whole events and keeps a partial one for the next chunk", () => {
    const a = parseSse("", ev("delta", { text: "Hi " }) + 'event: delta\ndata: {"te');
    expect(a.events).toEqual([{ name: "delta", data: { text: "Hi " } }]);
    const b = parseSse(a.rest, 'xt":"there"}\n\n');
    expect(b.events).toEqual([{ name: "delta", data: { text: "there" } }]);
    expect(b.rest).toBe("");
  });

  it("drops an event whose data is not JSON", () => {
    expect(parseSse("", "event: delta\ndata: nope\n\n").events).toEqual([]);
  });
});

describe("reduce", () => {
  const meta = { thread_id: "t1", path: "local_model", proposal_id: 7 };
  const run = (...a: Parameters<typeof reduce>[1][]): Chat => a.reduce(reduce, initial);

  it("streams deltas into one assistant message and ends on done", () => {
    const s = run(
      { type: "send", text: "How much?" },
      { type: "event", event: { name: "meta", data: meta } },
      { type: "event", event: { name: "delta", data: { text: "You spent " } } },
      { type: "event", event: { name: "delta", data: { text: "£10.00" } } },
      { type: "event", event: { name: "done", data: {} } },
    );
    expect(s.thread).toBe("t1");
    expect(s.busy).toBe(false);
    expect(s.messages.map((m) => [m.role, m.text])).toEqual([
      ["user", "How much?"],
      ["assistant", "You spent £10.00"],
    ]);
    expect(s.messages[1]).toMatchObject({ path: "local_model", proposalId: 7, streaming: false });
  });

  it("a failed request ends the turn with an error message", () => {
    const s = run({ type: "send", text: "x" }, { type: "fail", error: "The local model is not answering." });
    expect(s.busy).toBe(false);
    expect(s.messages.at(-1)).toMatchObject({ role: "assistant", error: "The local model is not answering." });
  });

  it("a decision is recorded and starts a turn", () => {
    const s = run(
      { type: "send", text: "x" },
      { type: "event", event: { name: "meta", data: meta } },
      { type: "event", event: { name: "done", data: {} } },
      { type: "decide", proposalId: 7, decision: "confirm" },
    );
    expect(s.decisions[7]).toBe("confirm");
    expect(s.busy).toBe(true);
  });

  it("reset forgets the thread", () => {
    expect(run({ type: "send", text: "x" }, { type: "reset" })).toEqual(initial);
  });
});

describe("pathLabel", () => {
  it("names the path that answered", () => {
    expect(pathLabel("cache", "hello")).toBe("Cache");
    expect(pathLabel("local_model", "hello")).toBe("Local model");
    expect(pathLabel("deterministic", "| a |\n| --- |\n| 1 |")).toBe("Table");
    expect(pathLabel("deterministic", "I can't help.")).toBe("Built in");
  });
});

describe("parseBlocks", () => {
  it("splits text and a Markdown table; markup stays text", () => {
    const b = parseBlocks("Here:\n\n| Item | Value |\n| --- | --- |\n| A \\| B | £1.00 |\n| <b>x</b> | 2 |");
    expect(b).toEqual([
      { kind: "text", text: "Here:" },
      {
        kind: "table",
        head: ["Item", "Value"],
        rows: [
          ["A | B", "£1.00"],
          ["<b>x</b>", "2"],
        ],
      },
    ]);
  });

  it("a lone pipe line is text", () => {
    expect(parseBlocks("a | b")).toEqual([{ kind: "text", text: "a | b" }]);
  });
});

describe("friendlyLabel", () => {
  it("names a spending summary figure by month and field", () => {
    expect(friendlyLabel("spending_summary 2026-03 money_in")).toBe("March 2026 · Money in");
    expect(friendlyLabel("spending_summary 2026-04 unmatched_refunds")).toBe("April 2026 · Unmatched refunds");
    expect(friendlyLabel("spending_summary 2026-04 month")).toBe("April 2026 · Month");
  });

  it("names a category amount by its category", () => {
    expect(friendlyLabel("spending_summary 2026-03 categories Food & Drink amount")).toBe("March 2026 · Food & Drink");
  });

  it("names transaction rows by day and field", () => {
    expect(friendlyLabel("find_transactions rows 2026-04-30 outflow")).toBe("30 April 2026 · Outflow");
    expect(friendlyLabel("find_transactions rows 2026-04-30 posted_on")).toBe("30 April 2026 · Posted");
    expect(friendlyLabel("find_transactions total")).toBe("Transactions found");
  });

  it("prints anything it does not recognise as it came", () => {
    expect(friendlyLabel("something_else 2026-03 odd_field")).toBe("something_else 2026-03 odd_field");
    expect(friendlyLabel("spending_summary 2026-03 brand_new_field")).toBe("spending_summary 2026-03 brand_new_field");
    expect(friendlyLabel("spending_summary 2026-13 net")).toBe("spending_summary 2026-13 net");
  });
});
