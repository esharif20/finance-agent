import { existsSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { routeTable, viewFor } from "@/main";
import { stopAt } from "./Flow";
import { FLOW, GROUPS, NODES, SAMPLE } from "./nodes";
import { NEXT } from "./WhatsNext";

const repo = path.resolve(import.meta.dirname, "../../..");

describe("architecture content", () => {
  it("cites a source file that exists for every node", () => {
    for (const n of NODES) {
      expect(n.sources.length, n.id).toBeGreaterThan(0);
      for (const s of n.sources) expect(existsSync(path.join(repo, s)), `${n.id}: ${s}`).toBe(true);
    }
  });

  it("gives every node a decision, a reason and at least one rejected alternative", () => {
    for (const n of NODES) {
      expect(n.decision.length, n.id).toBeGreaterThan(40);
      expect(n.why.length, n.id).toBeGreaterThan(20);
      expect(n.rejected.length, n.id).toBeGreaterThan(0);
    }
  });

  it("has unique ids, and nodes in every group", () => {
    expect(new Set(NODES.map((n) => n.id)).size).toBe(NODES.length);
    for (const g of GROUPS) expect(NODES.some((n) => n.group === g.id), g.id).toBe(true);
  });

  it("narrates one step per node of the question's path", () => {
    expect(FLOW.map((n) => n.id)).toEqual(["planner", "tools", "writer", "grounding", "reply"]);
    expect(SAMPLE.steps).toHaveLength(FLOW.length);
  });

  it("maps a scroll position to one stop of the scene", () => {
    expect(stopAt(-0.5)).toBe(-1);
    expect(stopAt(0)).toBe(0);
    expect(stopAt(2.99)).toBe(2);
    expect(stopAt(FLOW.length)).toBe(FLOW.length - 1);
  });

  it("marks every Planned item of What's next with its issue", () => {
    for (const g of NEXT) for (const it of g.items) if (it.kind === "planned") expect(it.issues?.length, it.title).toBeGreaterThan(0);
  });

  it("is served at /architecture in the demo build only", () => {
    expect(viewFor("/architecture", true)).toBe("architecture");
    expect(viewFor("/architecture", false)).toBe("app");
    expect(Object.values(routeTable(false))).not.toContain("architecture");
  });
});
