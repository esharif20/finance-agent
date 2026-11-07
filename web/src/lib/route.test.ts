import { describe, expect, it } from "vitest";
import { parseHash } from "./route";

describe("parseHash", () => {
  it("routes #review to the Review Queue", () => {
    expect(parseHash("#review")).toEqual({ name: "review" });
  });
});
