import { describe, expect, it } from "vitest";
import { cn } from "./utils";

describe("cn", () => {
  it("treats the type-scale tokens as sizes, so a colour class does not remove them", () => {
    for (const size of ["hero", "figure", "title", "heading", "row", "label", "meta"]) {
      expect(cn(`text-${size}`, "text-muted-foreground")).toBe(`text-${size} text-muted-foreground`);
    }
  });

  it("still lets a later size replace an earlier one", () => {
    expect(cn("text-label", "text-meta")).toBe("text-meta");
  });
});
