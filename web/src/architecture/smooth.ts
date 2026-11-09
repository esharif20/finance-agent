/* One scroll driver for the page: Lenis smooths the wheel on a wide screen with a fine pointer and
   no reduced-motion preference, and ScrollTrigger reads Lenis on the same GSAP tick. Touch, phones
   and reduced motion keep native scroll, and ScrollTrigger reads that directly. */
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import Lenis from "lenis";
import { useEffect } from "react";

gsap.registerPlugin(useGSAP, ScrollTrigger);

let lenis: Lenis | null = null;

/** The running Lenis instance, or null when the page scrolls natively. */
export const smoothScroller = () => lenis;

export function useSmoothScroll(on: boolean) {
  useEffect(() => {
    // every browser that runs Lenis has ResizeObserver; jsdom does not, so tests keep native scroll
    if (!on || typeof ResizeObserver === "undefined") return;
    const l = new Lenis({ anchors: true, lerp: 0.12 });
    lenis = l;
    const tick = (time: number) => l.raf(time * 1000);
    l.on("scroll", ScrollTrigger.update);
    gsap.ticker.add(tick);
    gsap.ticker.lagSmoothing(0);
    return () => {
      gsap.ticker.remove(tick);
      gsap.ticker.lagSmoothing(500, 33);
      l.destroy();
      lenis = null;
    };
  }, [on]);
}

export { gsap, ScrollTrigger, useGSAP };
