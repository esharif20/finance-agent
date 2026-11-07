// Procedural payment card (#58), built with the img2threejs pipeline from the user's
// card images as references (the images never enter the repo). Generic only: body,
// bevelled edge, face shell, chip with its channels. Every bank-specific mark arrives
// as a runtime texture from the user's local file; none is drawn here.
import * as THREE from "three";
import { ASPECT, RADIUS } from "@/lib/cardArt";

// Reconstruction data (from the sculpt spec), kept apart from the renderer objects.
// Units are card widths; the model is built in CSS px so it lines up with the DOM.
export const CARD_SPEC = {
  thickness: 0.026, // about 3x ID-1's 0.76mm, so the edge reads at UI size (stylised)
  bevel: 0.4, // of the thickness
  inset: 0.55, // of the bevel: the face stops short of the edge so the lit rim shows at rest
  chip: { w: 0.128, h: 0.105, r: 0.014, x: -0.33, y: 0.04, depth: 0.0012 },
  channels: [
    // [x, y, length, vertical]: the engraved lines between the six contact pads
    [-0.012, 0, 0.105, true],
    [0.026, 0.017, 0.076, false],
    [0.026, -0.017, 0.076, false],
    [-0.038, 0.017, 0.052, false],
    [-0.038, -0.017, 0.052, false],
  ] as const,
  materials: {
    body: { color: "#9aa2ab", roughness: 0.3, clearcoat: 0.6, clearcoatRoughness: 0.2 },
    face: { roughness: 0.45, clearcoat: 0.3, clearcoatRoughness: 0.35 },
    chip: { color: "#e6d6a4", metalness: 0.7, roughness: 0.35 },
    channel: { color: "#6f6243", metalness: 0.6, roughness: 0.6 },
  },
};

function roundedRect(w: number, h: number, r: number): THREE.Shape {
  const s = new THREE.Shape();
  const x = -w / 2;
  const y = -h / 2;
  s.moveTo(x + r, y);
  s.lineTo(x + w - r, y);
  s.quadraticCurveTo(x + w, y, x + w, y + r);
  s.lineTo(x + w, y + h - r);
  s.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  s.lineTo(x + r, y + h);
  s.quadraticCurveTo(x, y + h, x, y + h - r);
  s.lineTo(x, y + r);
  s.quadraticCurveTo(x, y, x + r, y);
  return s;
}

/** Planar UVs over the shape's bounds, so a card image maps 1:1 onto the face. */
function planarUV(g: THREE.BufferGeometry, w: number, h: number) {
  const pos = g.getAttribute("position") as THREE.BufferAttribute;
  const uv = new Float32Array(pos.count * 2);
  for (let i = 0; i < pos.count; i++) {
    uv[i * 2] = pos.getX(i) / w + 0.5;
    uv[i * 2 + 1] = pos.getY(i) / h + 0.5;
  }
  g.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
}

export type CardParts = ReturnType<typeof createCardParts>;

/** Geometry and materials shared by every card of one size (height defaults to ID-1). */
export function createCardParts(width: number, height = width / ASPECT) {
  const w = width;
  const h = height;
  const r = w * RADIUS;
  const t = w * CARD_SPEC.thickness;
  const b = t * CARD_SPEC.bevel;
  const body = new THREE.ExtrudeGeometry(roundedRect(w - 2 * b, h - 2 * b, r - b), {
    depth: t - 2 * b,
    bevelEnabled: true,
    bevelThickness: b,
    bevelSize: b,
    bevelSegments: 3,
    curveSegments: 10,
  });
  body.translate(0, 0, -(t - b) - 0.3); // front just behind the face shell at z = 0
  const i = b * CARD_SPEC.inset;
  const face = new THREE.ShapeGeometry(roundedRect(w - 2 * i, h - 2 * i, r - i), 10);
  planarUV(face, w - 2 * i, h - 2 * i);
  const c = CARD_SPEC.chip;
  const chip = new THREE.ExtrudeGeometry(roundedRect(c.w * w, c.h * w, c.r * w), {
    depth: c.depth * w,
    bevelEnabled: false,
    curveSegments: 4,
  });
  const m = CARD_SPEC.materials;
  // A soft contact shadow under each card, drawn in the scene so it only falls on cards
  // behind it (a DOM shadow would paint over the card in front).
  const pad = 56;
  const sc = document.createElement("canvas");
  sc.width = 256 + 2 * pad;
  sc.height = Math.round(256 / (w / h)) + 2 * pad;
  const g = sc.getContext("2d");
  if (g) {
    g.shadowColor = "rgb(0 0 0 / 0.9)";
    g.shadowBlur = 34;
    g.shadowOffsetX = 4000; // draw the shape off-canvas so only its blur lands here
    g.fillRect(pad - 4000, pad, 256, sc.height - 2 * pad);
  }
  const shadowMap = new THREE.CanvasTexture(sc);
  const shadow = new THREE.PlaneGeometry(w * (sc.width / 256), h * (sc.height / (sc.height - 2 * pad)));
  // The label scrim ramp: texture v = 0 (canvas bottom) clear, v = 1 dark; `setScrim` maps it.
  const sg = document.createElement("canvas");
  sg.width = 4;
  sg.height = 256;
  const s2 = sg.getContext("2d");
  if (s2) {
    const grad = s2.createLinearGradient(0, 0, 0, 256);
    grad.addColorStop(0, "rgb(6 8 10 / 1)"); // `setScrim`'s strength sets the darkness
    grad.addColorStop(1, "rgb(0 0 0 / 0)");
    s2.fillStyle = grad;
    s2.fillRect(0, 0, 4, 256);
  }
  const scrimMap = new THREE.CanvasTexture(sg);
  // No mipmaps: squeezed by the repeat, a tiny ramp's mip levels streak across the face.
  scrimMap.generateMipmaps = false;
  scrimMap.minFilter = THREE.LinearFilter;
  return {
    w,
    h,
    body,
    face,
    chip,
    channel: new THREE.BoxGeometry(1, 1, 1),
    bodyMaterial: new THREE.MeshPhysicalMaterial(m.body),
    chipMaterial: new THREE.MeshPhysicalMaterial(m.chip),
    channelMaterial: new THREE.MeshStandardMaterial(m.channel),
    shadow,
    shadowMaterial: new THREE.MeshBasicMaterial({ map: shadowMap, transparent: true, opacity: 0.5, depthWrite: false }),
    scrimMap,
    dispose() {
      for (const x of [body, face, chip, shadow, shadowMap, scrimMap, this.channel, this.bodyMaterial, this.chipMaterial, this.channelMaterial, this.shadowMaterial])
        x.dispose();
    },
  };
}

/** One card: body, face (with `texture`), the chip unless the art prints its own, and the
    back (with `back`, the face faded towards the page; #93). */
export function createPaymentCardModel(parts: CardParts, texture: THREE.Texture, withChip: boolean, back: THREE.Texture) {
  const group = new THREE.Group();
  group.name = "payment-card";
  // polygonOffset: the face sits a hair in front of the body; without it they z-fight when tilted
  const faceMaterial = new THREE.MeshPhysicalMaterial({
    ...CARD_SPEC.materials.face,
    map: texture,
    polygonOffset: true,
    polygonOffsetFactor: -1,
    polygonOffsetUnits: -2,
  });
  const face = new THREE.Mesh(parts.face, faceMaterial);
  face.name = "card-face";
  const shadow = new THREE.Mesh(parts.shadow, parts.shadowMaterial);
  shadow.position.set(0, -16, -parts.w * CARD_SPEC.thickness - 2);
  // Darkens the face under the DOM labels (see `setScrim`); hidden until a card needs one.
  const scrimMap = parts.scrimMap.clone();
  const scrimMaterial = new THREE.MeshBasicMaterial({
    map: scrimMap,
    transparent: true,
    depthWrite: false,
    polygonOffset: true,
    polygonOffsetFactor: -2,
    polygonOffsetUnits: -4,
  });
  const scrim = new THREE.Mesh(parts.face, scrimMaterial);
  scrim.name = "label-scrim";
  scrim.visible = false;
  // The face's own shape and UVs, drawn from behind: a turned card shows its face mirrored, as
  // if printed through. Unlit, so the fade reads as the page colour whatever the light.
  const backMaterial = new THREE.MeshBasicMaterial({ map: back, side: THREE.BackSide, toneMapped: false });
  const backFace = new THREE.Mesh(parts.face, backMaterial);
  backFace.name = "card-back";
  backFace.position.z = -parts.w * CARD_SPEC.thickness - 0.6; // the body's back is at -t - 0.3
  group.add(shadow, new THREE.Mesh(parts.body, parts.bodyMaterial), face, scrim, backFace);
  if (withChip) {
    const c = CARD_SPEC.chip;
    const w = parts.w;
    const chip = new THREE.Group();
    chip.name = "chip";
    chip.position.set(c.x * w, c.y * w, 0.05);
    chip.add(new THREE.Mesh(parts.chip, parts.chipMaterial));
    for (const [x, y, len, vertical] of CARD_SPEC.channels) {
      const ch = new THREE.Mesh(parts.channel, parts.channelMaterial);
      ch.scale.set(vertical ? 0.004 * w : len * w, vertical ? len * w : 0.004 * w, 0.6);
      ch.position.set(x * w, y * w, c.depth * w);
      chip.add(ch);
    }
    group.add(chip);
  }
  return {
    group,
    h: parts.h,
    /** Scrim from `top` px below the card's top edge (clear above, dark from top + fade down),
        or none. */
    setScrim(top: number | null, fade = 28, strength = 1) {
      scrim.visible = top !== null;
      if (top === null) return;
      scrimMaterial.opacity = strength;
      const vTop = 1 - top / parts.h; // planar UVs: v = 1 at the top edge
      const f = fade / parts.h;
      scrimMap.repeat.set(1, -1 / f); // v = vTop -> clear, v = vTop - f -> dark, darker below clamps
      scrimMap.offset.set(0, vTop / f);
    },
    dispose() {
      faceMaterial.dispose();
      texture.dispose();
      scrimMaterial.dispose();
      scrimMap.dispose();
      backMaterial.dispose();
      back.dispose();
    },
  };
}
