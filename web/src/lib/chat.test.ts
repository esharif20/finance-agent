import { describe, expect, it } from "vitest";
import { type Chat, initial, parseBlocks, parseSse, pathLabel, reduce } from "./chat";

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
