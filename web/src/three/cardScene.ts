// The account stack's single WebGL canvas (#58). Lazy-loaded: three.js is only fetched
// when motion is allowed and WebGL works. Renders on demand; the camera matches the
// CSS perspective of the stack so each DOM card (text, focus, clicks) sits exactly on
// its 3D card.
import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import type { Pose } from "@/lib/cardPoses";
import { type CardParts, createCardParts, createPaymentCardModel } from "./cardModel";

export type SceneFace = { art: HTMLCanvasElement | null; hue: number | null };

/** The generic face: the bank's colour, a few diagonal bands and a contactless mark. */
function genericFace(hue: number | null, w: number, h: number): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const g = c.getContext("2d");
  if (!g) return c;
  const bg = g.createLinearGradient(0, 0, w, h);
  if (hue === null) {
    bg.addColorStop(0, "oklch(0.38 0.02 205)");
    bg.addColorStop(1, "oklch(0.26 0.02 205)");
  } else {
    bg.addColorStop(0, `oklch(0.47 0.12 ${hue})`);
    bg.addColorStop(1, `oklch(0.31 0.085 ${hue + 18})`);
  }
  g.fillStyle = bg;
  g.fillRect(0, 0, w, h);
  g.save();
  g.translate(w * 0.62, 0);
  g.rotate(-0.5);
  g.fillStyle = "rgb(255 255 255 / 0.07)";
  for (let i = 0; i < 3; i++) g.fillRect(i * w * 0.11, -h, w * 0.05 + i * w * 0.012, h * 3);
  g.restore();
  if (hue !== null) {
    g.strokeStyle = "rgb(255 255 255 / 0.7)";
    g.lineWidth = w * 0.006;
    g.lineCap = "round";
    const cx = w * 0.88;
    const cy = h * 0.5;
    for (let i = 1; i <= 4; i++) {
      g.beginPath();
      g.arc(cx - w * 0.05, cy, w * 0.013 * i, -0.75, 0.75);
      g.stroke();
    }
  }
  return c;
}

export type CardScene = Awaited<ReturnType<typeof createCardScene>>;

export function createCardScene(canvas: HTMLCanvasElement, perspective: number) {
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: "low-power" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.toneMapping = THREE.NeutralToneMapping; // keeps bank colours true
  renderer.toneMappingExposure = 1;
  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  const env = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environment = env;
  scene.environmentIntensity = 0.15;
  const key = new THREE.DirectionalLight(0xffffff, 0.85);
  key.position.set(-0.6, 1.2, 2);
  const glint = new THREE.PointLight(0xffffff, 0, 0, 2); // follows the pointer: the sheen
  scene.add(key, glint, new THREE.HemisphereLight(0xffffff, 0x8a8f96, 0.25));
  const camera = new THREE.PerspectiveCamera(30, 1, 1, perspective * 4);
  camera.position.set(0, 0, perspective);

  let parts: CardParts | null = null;
  let cards: { group: THREE.Group; dispose(): void }[] = [];
  let faces: SceneFace[] = [];
  let box = { w: 0, h: 0 };
  const order: number[] = [];

  function build() {
    for (const c of cards) {
      scene.remove(c.group);
      c.dispose();
    }
    parts?.dispose();
    parts = createCardParts(box.w);
    const p = parts;
    cards = faces.map((f) => {
      const src = f.art ?? genericFace(f.hue, 1024, Math.round(1024 / (p.w / p.h)));
      const tex = new THREE.CanvasTexture(src);
      tex.colorSpace = THREE.SRGBColorSpace;
      tex.anisotropy = 4;
      const card = createPaymentCardModel(p, tex, f.art === null && f.hue !== null);
      scene.add(card.group);
      return card;
    });
  }

  return {
    /** Size of the stack; the canvas overflows it by mx at each side and my top and bottom. */
    setSize(w: number, h: number, mx: number, my: number) {
      const rebuild = w !== box.w;
      box = { w, h };
      const cw = w + 2 * mx;
      const ch = h + 2 * my;
      renderer.setSize(cw, ch, false);
      camera.aspect = cw / ch;
      camera.fov = (2 * Math.atan(ch / 2 / perspective) * 180) / Math.PI;
      camera.updateProjectionMatrix();
      if (rebuild && faces.length) build();
    },
    setFaces(next: SceneFace[]) {
      faces = next;
      if (box.w) build();
    },
    /** Same numbers as the DOM transforms, converted from CSS (y down) to three (y up). */
    update(poses: Pose[], light: { x: number; y: number; on: number }) {
      if (!parts) return;
      const { w, h } = box;
      cards.forEach((c, i) => {
        const p = poses[i];
        if (!p) return;
        c.group.position.set(parts!.w / 2 + p.x - w / 2, -(parts!.h / 2 + p.y - h / 2), p.z);
        c.group.rotation.set((-p.rx * Math.PI) / 180, (p.ry * Math.PI) / 180, (-p.rz * Math.PI) / 180);
        c.group.scale.setScalar(p.s);
        order[i] = p.layer;
      });
      glint.position.set(light.x - w / 2, -(light.y - h / 2), 700);
      glint.intensity = 90000 * light.on; // falls off with distance (px), so it stays a highlight
    },
    /** Card by card, back to front, clearing depth between them: like the DOM's z-index, a
        tilted card never pokes through the one in front of it. */
    render() {
      renderer.autoClear = false;
      renderer.clear();
      const back = cards.map((c, i) => ({ c, layer: order[i] ?? i })).sort((a, b) => a.layer - b.layer);
      for (const { c } of back) {
        for (const o of cards) o.group.visible = o === c;
        renderer.clearDepth();
        renderer.render(scene, camera);
      }
      for (const o of cards) o.group.visible = true;
    },
    dispose() {
      for (const c of cards) c.dispose();
      parts?.dispose();
      env.dispose();
      pmrem.dispose();
      renderer.dispose();
    },
  };
}
