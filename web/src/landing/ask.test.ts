import { describe, expect, it } from "vitest";
import { EXAMPLES, reply } from "./Ask";
import { OVERVIEW } from "./story/story.fixture";

describe("ask: every figure comes from the fixture", () => {
  it("answers each example from a figure the fixture holds", () => {
    for (const e of EXAMPLES) expect(reply(e).kind).not.toBe("none");
    expect(reply("Coffee in October")).toEqual({ kind: "category", name: "Eating out", amount: OVERVIEW.top[2]?.amount });
  });

  it("says it cannot answer rather than guessing", () => {
    for (const q of ["What did I pay in rent?", "my pension", "", "£999 at the casino", "<script>alert(1)</script>"])
      expect(reply(q).kind).toBe("none");
  });
});
