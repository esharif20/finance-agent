import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PrivacyProvider, usePrivacy } from "@/lib/privacy";
import { useCardArt } from "./useCardArt";

// #129: the art hook loads each key once and, like the stack it came from, shows no art while
// privacy mode hides amounts. Synthetic keys only; the art is a stand-in canvas.
const fake = document.createElement("canvas");
const loadArt = vi.fn(async (key: string) => (key === "known" ? fake : null));
vi.mock("@/lib/cardArt", async (original) => ({
  ...(await original<typeof import("@/lib/cardArt")>()),
  loadArt: (key: string) => loadArt(key),
}));

afterEach(() => {
  loadArt.mockClear();
  localStorage.removeItem("fa-private");
  document.documentElement.removeAttribute("data-private");
});

const cards = [{ art: null }, { art: "known" }, { art: "known" }, { art: "missing" }];

it("loads each art key once and keeps only the art that exists", async () => {
  const { result } = renderHook(() => useCardArt(cards), { wrapper: PrivacyProvider });
  await vi.waitFor(() => expect(result.current).toEqual({ known: fake }));
  expect(loadArt.mock.calls.map(([k]) => k).sort()).toEqual(["known", "missing"]);
});

it("shows no art while privacy mode is on, and the art again once it is off", async () => {
  const { result } = renderHook(() => ({ art: useCardArt(cards), privacy: usePrivacy() }), {
    wrapper: PrivacyProvider,
  });
  await vi.waitFor(() => expect(result.current.art).toEqual({ known: fake }));
  act(() => result.current.privacy.toggle());
  expect(result.current.art).toEqual({});
  act(() => result.current.privacy.toggle());
  expect(result.current.art).toEqual({ known: fake });
});
