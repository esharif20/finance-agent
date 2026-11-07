import { describe, expect, it } from "vitest";
import { MANAGE_LINKS, isSubscription, manageUrl } from "./manageLinks";

describe("manage links", () => {
  it("every entry is https on the entry's own domain", () => {
    for (const e of MANAGE_LINKS) {
      const u = new URL(e.url);
      expect(u.protocol).toBe("https:");
      expect(u.hostname === e.domain || u.hostname.endsWith(`.${e.domain}`)).toBe(true);
      expect(manageUrl(e.names[0])).toBe(e.url);
    }
  });
  it("matches normalised Merchant names however the Bank spelled them", () => {
    expect(manageUrl("NETFLIX.COM")).toBe("https://www.netflix.com/youraccount");
    expect(manageUrl("Apple.com/BILL")).toBe("https://apps.apple.com/account/subscriptions");
    expect(manageUrl("APPLE COM BILL")).toBe("https://apps.apple.com/account/subscriptions");
  });
  it("gives nothing for an unknown Merchant, and never guesses from a look-alike", () => {
    expect(manageUrl("Obscure Gym Ltd")).toBeNull();
    expect(manageUrl("Netflix Phishing")).toBeNull();
    expect(manageUrl(null)).toBeNull();
    expect(manageUrl("")).toBeNull();
  });
  it("takes a URL only from the built-in list, never from the name it is given", () => {
    for (const evil of ["https://evil.example/netflix", "http://netflix.com.evil.example", "evil.example"]) {
      expect(manageUrl(evil)).toBeNull();
    }
    const all = new Set(MANAGE_LINKS.map((e) => e.url));
    for (const n of MANAGE_LINKS.flatMap((e) => e.names)) expect(all.has(manageUrl(n) as string)).toBe(true);
  });
  it("recognises Subscriptions, Streaming and their children", () => {
    expect(isSubscription(["Subscriptions", "Software"])).toBe(true);
    expect(isSubscription(["Subscriptions", "Streaming"])).toBe(true);
    expect(isSubscription(["Streaming"])).toBe(true);
    expect(isSubscription(["Shopping", "Online"])).toBe(false);
    expect(isSubscription(null)).toBe(false);
  });
});
