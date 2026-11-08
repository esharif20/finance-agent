import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MotionFx } from "@/components/MotionFx";
import { Card } from "@/components/ui/card";

const io = { observe: vi.fn(), unobserve: vi.fn(), disconnect: vi.fn() };
const stubMedia = (reduce: boolean) =>
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("reduce") ? reduce : false, media: q, addEventListener() {}, removeEventListener() {} }));

afterEach(() => vi.unstubAllGlobals());

describe("MotionFx scroll reveal", () => {
  it("never hides a card when there is no IntersectionObserver to show it again", () => {
    stubMedia(false);
    vi.stubGlobal("IntersectionObserver", undefined);
    const { container } = render(<><MotionFx /><Card>Net</Card></>);
    expect(container.querySelector("[data-reveal]")).toBeNull();
  });

  it("holds a card for its reveal only when motion is allowed", () => {
    stubMedia(false);
    vi.stubGlobal("IntersectionObserver", vi.fn(function () { return io; }));
    const { container } = render(<><Card>Net</Card><MotionFx /></>);
    expect(container.querySelector(".card-glass")).toHaveAttribute("data-reveal", "pending");
  });

  it("does nothing under reduced motion", () => {
    stubMedia(true);
    vi.stubGlobal("IntersectionObserver", vi.fn(function () { return io; }));
    const { container } = render(<><Card>Net</Card><MotionFx /></>);
    expect(container.querySelector("[data-reveal]")).toBeNull();
  });
});
