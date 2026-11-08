/** The Assistant conversation, held above the router: a turn in flight keeps
 * streaming into it while another tab is open, and the screen re-attaches when
 * it comes back. The transcript is kept in sessionStorage (this tab only, gone
 * when it closes) so a reload restores it; nothing here is logged. */

import { useSyncExternalStore } from "react";
import { type Action, type Chat, type Decision, initial, parseSse, reduce } from "./chat";

const KEY = "fa.assistant.chat";
export const INTERRUPTED = "This answer was interrupted when the page reloaded. Ask again.";

export const refusal = (status: number) =>
  status === 404 ? "That conversation has ended. Ask again to start a new one."
  : status === 409 ? "Nothing is waiting for confirmation."
  : status === 422 ? "That message can't be sent. Keep it under 2,000 characters."
  : import.meta.env.VITE_DEMO === "1" ? "The Assistant isn't available right now. Try again in a moment."
  : "The Assistant isn't available right now. Check that the local model is running.";

function load(): Chat {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return initial;
    const c = JSON.parse(raw) as Chat;
    if (!Array.isArray(c.messages)) return initial;
    // a reload drops the fetch: a turn still streaming then can't finish here
    const messages = c.messages.map((m) =>
      m.streaming ? { ...m, streaming: false, stage: undefined, error: m.text ? undefined : INTERRUPTED } : m,
    );
    return { ...initial, ...c, messages, busy: false };
  } catch {
    return initial;
  }
}

let state: Chat = load();
const listeners = new Set<() => void>();

function dispatch(a: Action) {
  state = reduce(state, a);
  try {
    sessionStorage.setItem(KEY, JSON.stringify(state));
  } catch {
    /* storage full or blocked: the chat still works for this page */
  }
  for (const l of listeners) l();
}

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};

export const useChat = () => useSyncExternalStore(subscribe, () => state);

async function turn(url: string, body: object) {
  const sent = performance.now();
  try {
    const res = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    if (!res.ok || !res.body) {
      if (res.status === 404) dispatch({ type: "reset" });
      return dispatch({ type: "fail", error: refusal(res.status) });
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let rest = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      const parsed = parseSse(rest, dec.decode(value, { stream: true }));
      rest = parsed.rest;
      for (const event of parsed.events) {
        if (event.name === "error") return dispatch({ type: "fail", error: refusal(500) });
        dispatch({ type: "event", event });
      }
    }
    dispatch({ type: "event", event: { name: "done", data: { waited_ms: Math.round(performance.now() - sent) } } });
  } catch {
    dispatch({ type: "fail", error: refusal(500) });
  }
}

/** Send a message; false when it can't be sent now. The fetch outlives the screen. */
export function send(text: string, cloud?: boolean): boolean {
  if (!text || state.busy) return false;
  const thread = state.thread;
  dispatch({ type: "send", text });
  const body: Record<string, unknown> = thread ? { message: text, thread_id: thread } : { message: text };
  if (cloud !== undefined) body.cloud = cloud;
  void turn("/api/chat", body);
  return true;
}

export function decide(proposalId: number, decision: Decision) {
  if (!state.thread || state.busy) return;
  const thread = state.thread;
  dispatch({ type: "decide", proposalId, decision });
  void turn(`/api/chat/${thread}/resume`, { decision });
}

/** Tests only: a fresh conversation, re-read from storage. */
export function resetChatStore() {
  state = load();
  for (const l of listeners) l();
}
