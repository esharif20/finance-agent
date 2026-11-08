import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SpendMeter } from "@/components/SpendMeter";
import { PrivacyProvider } from "@/lib/privacy";
import { Assistant } from "@/screens/Assistant";

// synthetic
const ev = (name: string, data: object) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;
const sse = (meta: object) =>
  new Response([ev("meta", { thread_id: "a".repeat(32), ...meta }), ev("delta", { text: "Fine." }), ev("done", {})].join(""), {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200 });
const bucket = (o: object) => ({ bucket: "gateway_chat", cap_pence: 1000, spent_pence: 0, reserved_pence: 0, state: "ok", ...o });

const wrap = (ui: React.ReactElement) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PrivacyProvider>{ui}</PrivacyProvider>
    </QueryClientProvider>,
  );

let spend: object[];
let chatBodies: Record<string, unknown>[];
let nextReply: object;
beforeEach(() => {
  spend = [bucket({})];
  chatBodies = [];
  nextReply = { path: "local_model" };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/spend") return json(spend);
      if (url.startsWith("/api/gateway/payloads"))
        return json([{ conversation_id: "a", cost_pence: 3, model: "m", payload: { facts: [{ field: "merchant", value: "MERCHANT_1" }] }, sent_at: "2026-01-01T00:00:00Z" }]);
      chatBodies.push(JSON.parse(String(init?.body)));
      return sse(nextReply);
    }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.resetModules();
});

const send = async (q = "Groceries?") => {
  await userEvent.type(screen.getByLabelText("Message the Assistant"), q);
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
};

describe("cloud toggle", () => {
  it("is off by default and sends cloud: true only after the click", async () => {
    wrap(<Assistant />);
    const toggle = screen.getByRole("switch", { name: /cloud model for this conversation/i });
    expect(toggle).not.toBeChecked();
    await send();
    await screen.findByText("Fine.");
    expect(chatBodies[0]).not.toHaveProperty("cloud");
    await userEvent.click(toggle);
    await send("Again?");
    await vi.waitFor(() => expect(chatBodies).toHaveLength(2));
    expect(chatBodies[1]?.cloud).toBe(true);
  });

  it("is disabled with the reason once the cap is reached", async () => {
    spend = [bucket({ spent_pence: 1000, state: "capped" })];
    wrap(<Assistant />);
    expect(await screen.findByText(/budget is used up/i)).toBeInTheDocument();
    expect(screen.getByRole("switch")).toBeDisabled();
  });

  it("is absent in the demo build", async () => {
    vi.stubEnv("VITE_DEMO", "1");
    vi.resetModules();
    const { Assistant: DemoAssistant } = await import("@/screens/Assistant");
    wrap(<DemoAssistant />);
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });
});

describe("What was sent", () => {
  it("shows the placeholders and the cost, never real values", async () => {
    nextReply = { path: "cloud", cost_pence: 3, payload_id: "p1" };
    wrap(<Assistant />);
    await send();
    await userEvent.click(await screen.findByText(/What was sent/));
    expect(await screen.findByLabelText("Redacted payload")).toHaveTextContent("MERCHANT_1");
    expect(screen.getByText(/What was sent/)).toHaveTextContent("£0.03");
    expect(document.body).not.toHaveTextContent("Tesco");
  });
});

describe("spend meter", () => {
  it("shows pence in the money formatter and the warning at 80%", async () => {
    spend = [bucket({ spent_pence: 800, state: "warn" })];
    wrap(<SpendMeter />);
    expect(await screen.findByText("£8.00 of £10.00")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/80%/);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "80");
  });

  it("shows no warning under 80%", async () => {
    spend = [bucket({ spent_pence: 200 })];
    wrap(<SpendMeter />);
    await screen.findByText("£2.00 of £10.00");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
