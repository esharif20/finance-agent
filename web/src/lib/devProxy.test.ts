import { describe, expect, it } from "vitest";
import { apiTarget } from "./devProxy";

describe("apiTarget", () => {
  it("the local app proxies to its own API", () => {
    expect(apiTarget({})).toBe("http://127.0.0.1:8765");
  });

  it("a demo build needs an explicit demo server", () => {
    expect(() => apiTarget({ VITE_DEMO: "1" })).toThrow(/FA_DEMO_API/);
    expect(apiTarget({ VITE_DEMO: "1", FA_DEMO_API: "http://127.0.0.1:51234/" })).toBe("http://127.0.0.1:51234");
  });

  it("a demo build refuses the real app's port however it is spelled", () => {
    for (const t of ["http://127.0.0.1:8765", "http://localhost:8765/api", "http://[::1]:8765", "http://192.168.1.5:8765"])
      expect(() => apiTarget({ VITE_DEMO: "1", FA_DEMO_API: t })).toThrow(/8765/);
    expect(() => apiTarget({ VITE_DEMO: "1", FA_DEMO_API: "not a url" })).toThrow(/not a URL/);
  });
});
