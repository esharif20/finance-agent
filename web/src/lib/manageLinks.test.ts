import { describe, expect, it } from "vitest";
import { isSubscription, manageUrl } from "./manageLinks";

// synthetic: the real list is served by the API
const LINKS = [
  { names: ["Netflix", "Netflix.com"], domain: "netflix.com", url: "https://www.netflix.com/youraccount" },
  { names: ["Apple", "Apple.com/bill"], domain: "apple.com", url: "https://apps.apple.com/account/subscriptions" },
  { names: ["Broken"], domain: "broken.example", url: "http://broken.example/account" },
  { names: ["Hijack"], domain: "hijack.example", url: "https://evil.example/account" },
];

describe("manage links", () => {
  it("matches normalised Merchant names however the Bank spelled them", () => {
    expect(manageUrl("NETFLIX.COM", LINKS)).toBe("https://www.netflix.com/youraccount");
    expect(manageUrl("Apple.com/BILL", LINKS)).toBe("https://apps.apple.com/account/subscriptions");
    expect(manageUrl("APPLE COM BILL", LINKS)).toBe("https://apps.apple.com/account/subscriptions");
  });
  it("gives nothing for an unknown Merchant, and never guesses from a look-alike", () => {
    expect(manageUrl("Obscure Gym Ltd", LINKS)).toBeNull();
    expect(manageUrl("Netflix Phishing", LINKS)).toBeNull();
    expect(manageUrl(null, LINKS)).toBeNull();
    expect(manageUrl("", LINKS)).toBeNull();
  });
  it("gives nothing before the list has loaded", () => expect(manageUrl("Netflix", undefined)).toBeNull());
  it("takes a URL only from the list, never from the name it is given", () => {
    for (const evil of ["https://evil.example/netflix", "http://netflix.com.evil.example", "evil.example"]) {
      expect(manageUrl(evil, LINKS)).toBeNull();
    }
  });
  it("refuses a listed url that is not https or not on its own domain", () => {
    expect(manageUrl("Broken", LINKS)).toBeNull();
    expect(manageUrl("Hijack", LINKS)).toBeNull();
  });
  it("recognises Subscriptions, Streaming and their children", () => {
    expect(isSubscription(["Subscriptions", "Software"])).toBe(true);
    expect(isSubscription(["Subscriptions", "Streaming"])).toBe(true);
    expect(isSubscription(["Streaming"])).toBe(true);
    expect(isSubscription(["Shopping", "Online"])).toBe(false);
    expect(isSubscription(null)).toBe(false);
  });
});
