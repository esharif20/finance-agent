import { type RefObject, useEffect, useRef, useState } from "react";
import type { CardScene, SceneFace } from "@/three/cardScene";

/** The stack's one lazy-loaded WebGL scene on `canvas`, while `want` holds. `gl` is true while
    it draws; on a lost GPU context the scene is freed and the CSS cards take over, and when
    the browser restores the context a new scene takes over again (#129). The scene pauses
    while `stack` is off screen. */
export function useCardScene(
  canvas: RefObject<HTMLCanvasElement | null>,
  stack: RefObject<HTMLElement | null>,
  want: boolean,
  o: { perspective: number; width: number; height: number; mx: number; my: number; faces: SceneFace[]; page: string },
  redraw: () => void,
) {
  const [gl, setGl] = useState(false);
  const scene = useRef<CardScene | null>(null);
  const onScreen = useRef(true); // the scene skips frames while the stack is off screen
  const { perspective } = o;

  useEffect(() => {
    if (!want || !canvas.current) return;
    let live = true;
    let lostIt = false;
    let create: typeof import("@/three/cardScene").createCardScene | null = null;
    const el = canvas.current;
    const start = () => {
      if (!live || !create) return;
      scene.current = create(el, perspective);
      setGl(true);
    };
    const lost = (e: Event) => {
      e.preventDefault(); // without it the browser never restores the context
      lostIt = true;
      const s = scene.current;
      scene.current = null; // before dispose, so nothing draws on a freed scene
      s?.dispose();
      setGl(false);
    };
    const restored = () => {
      if (!lostIt) return; // only a context we lost: never a second scene
      lostIt = false;
      start();
    };
    el.addEventListener("webglcontextlost", lost);
    el.addEventListener("webglcontextrestored", restored);
    import("@/three/cardScene")
      .then((m) => {
        create = m.createCardScene;
        if (!lostIt) start();
      })
      .catch(() => setGl(false));
    return () => {
      live = false;
      el.removeEventListener("webglcontextlost", lost);
      el.removeEventListener("webglcontextrestored", restored);
      scene.current?.dispose();
      scene.current = null;
      setGl(false);
    };
  }, [want, canvas, perspective]);

  const { width, height, mx, my, faces, page } = o;
  useEffect(() => {
    const s = scene.current;
    if (!gl || !s) return;
    s.setSize(width, height, mx, my);
    s.setFaces(faces, page);
    redraw();
  }, [gl, width, height, mx, my, faces, page, redraw]);

  useEffect(() => {
    const el = stack.current;
    if (!gl || !el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(([e]) => {
      onScreen.current = e?.isIntersecting ?? true;
      if (onScreen.current) redraw();
    });
    io.observe(el);
    return () => io.disconnect();
  }, [gl, stack, redraw]);

  return { gl, scene, onScreen };
}
