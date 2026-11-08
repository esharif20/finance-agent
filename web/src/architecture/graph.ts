/* The map at the top of /architecture: every node, placed by hand around the question's path so the
   layout is fixed (no simulation runs in the browser), and edges taken from each node's `links`. */
import { FLOW, type GroupId, NODES } from "./nodes";

export const W = 1200;
export const H = 700;

/** wide positions in a 1200 x 700 box; the phone draws the same map turned on its side */
const AT: Record<string, [number, number]> = {
  planner: [150, 350],
  tools: [400, 350],
  writer: [650, 350],
  grounding: [880, 350],
  reply: [1070, 350],
  l0: [170, 150],
  l1: [330, 90],
  l2: [480, 160],
  l3: [320, 225],
  router: [100, 500],
  local: [660, 110],
  cloud: [840, 170],
  order: [560, 530],
  warm: [700, 470],
  cache: [700, 610],
  lora: [270, 480],
  bandit: [190, 620],
  beta: [350, 590],
  bt: [440, 480],
  bench: [970, 70],
  gate: [1110, 130],
  judge: [990, 210],
  ci: [1120, 245],
  egress: [1000, 470],
  placeholders: [870, 500],
  keychain: [1130, 520],
  sqlcipher: [990, 600],
  injection: [840, 640],
  nopay: [1140, 640],
  proposals: [420, 660],
};

/** names short enough to sit under a dot */
const LABEL: Record<string, string> = {
  tools: "Engine reads",
  writer: "Writer call",
  grounding: "Grounding check",
  reply: "Reply",
  l0: "L0 Profile",
  l1: "L1 Summaries",
  l2: "L2 Meaning",
  l3: "L3 Ledger",
  cloud: "Cloud wording",
  order: "Prompt order",
  bandit: "Thompson bandit",
  bt: "Bradley-Terry",
  beta: "Beta relevance",
  lora: "LoRA",
  bench: "Benchmark",
  gate: "Results gate",
  judge: "Judge",
  egress: "Three hosts",
  sqlcipher: "SQLCipher",
  placeholders: "Placeholders",
  injection: "Sanitising",
  proposals: "Proposals",
};

/** one colour per chapter, as the chapters below use them. No hue between 235 and 290. */
export const COLOUR: Record<GroupId, string> = {
  flow: "#f4f6f2",
  knowledge: "#93f1f7",
  models: "#f7a8cb", // hue 333
  context: "#f3ea7a",
  learning: "#9ae187",
  evals: "#ff9e8a", // hue 10
  security: "#fbb65c",
};

export interface Dot {
  id: string;
  group: GroupId;
  label: string;
  x: number;
  y: number;
}

export const DOTS: Dot[] = NODES.map((n) => {
  const p = AT[n.id] ?? [W / 2, H / 2];
  return { id: n.id, group: n.group, label: LABEL[n.id] ?? n.title, x: p[0], y: p[1] };
});

const pathIds = FLOW.map((n) => n.id);
const onPath = (a: string, b: string) => Math.abs(pathIds.indexOf(a) - pathIds.indexOf(b)) === 1 && pathIds.includes(a) && pathIds.includes(b);

export interface Edge {
  a: string;
  b: string;
  /** a step of the question's path, drawn flowing */
  path: boolean;
}

export const EDGES: Edge[] = NODES.flatMap((n) => n.links.map((b) => ({ a: n.id, b, path: onPath(n.id, b) })));

export const NEIGHBOURS: Record<string, Set<string>> = Object.fromEntries(NODES.map((n) => [n.id, new Set([n.id])]));
for (const e of EDGES) {
  NEIGHBOURS[e.a]?.add(e.b);
  NEIGHBOURS[e.b]?.add(e.a);
}

export const placed = (id: string) => id in AT;
