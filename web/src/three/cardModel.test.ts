import * as THREE from "three";
import { expect, it } from "vitest";
import { createCardParts, createPaymentCardModel } from "./cardModel";

// #93, #129: each card has a back that reads the right way round, and every GPU resource it
// adds is freed when the scene goes.
const textures = (m: THREE.Material) =>
  Object.values(m).filter((v): v is THREE.Texture => v instanceof THREE.Texture);

function build() {
  const parts = createCardParts(360);
  const front = new THREE.Texture();
  const back = new THREE.Texture();
  const card = createPaymentCardModel(parts, front, true, back);
  return { parts, front, back, card };
}

it("shows the back texture on the far side, reading left to right, never mirrored (#129)", () => {
  const { card, back, parts } = build();
  const mesh = card.group.getObjectByName("card-back") as THREE.Mesh;
  const m = mesh.material as THREE.MeshBasicMaterial;
  expect(m.map).toBe(back);
  const face = card.group.getObjectByName("card-face") as THREE.Mesh;
  mesh.updateMatrix();
  expect(mesh.position.z).toBeLessThan(face.position.z); // behind the body, not on the front
  // Seen from behind (looking along +z), the viewer's left is +x: it must show the texture's left.
  const pos = mesh.geometry.getAttribute("position") as THREE.BufferAttribute;
  const uv = mesh.geometry.getAttribute("uv") as THREE.BufferAttribute;
  const p = new THREE.Vector3();
  let checked = 0;
  for (let i = 0; i < pos.count; i++) {
    p.fromBufferAttribute(pos, i).applyMatrix4(mesh.matrix);
    if (Math.abs(p.x) < parts.w * 0.3) continue;
    expect(Math.sign(uv.getX(i) - 0.5)).toBe(-Math.sign(p.x));
    checked++;
  }
  expect(checked).toBeGreaterThan(8);
  // and its visible side faces away from the front (-z), so the camera sees it only when turned
  const n = new THREE.Vector3(0, 0, m.side === THREE.BackSide ? -1 : 1).applyQuaternion(mesh.quaternion);
  expect(n.z).toBeLessThan(0);
});

it("frees every geometry, material and texture of the card and its parts", () => {
  const { card, parts } = build();
  const owned = new Set<THREE.EventDispatcher<{ dispose: object }>>();
  card.group.traverse((o) => {
    if (!(o instanceof THREE.Mesh)) return;
    owned.add(o.geometry);
    for (const m of [o.material].flat() as THREE.Material[]) {
      owned.add(m);
      for (const t of textures(m)) owned.add(t);
    }
  });
  const freed = new Set<unknown>();
  for (const r of owned) r.addEventListener("dispose", () => freed.add(r));

  card.dispose();
  parts.dispose();

  expect(owned.size).toBeGreaterThan(8);
  expect([...owned].filter((r) => !freed.has(r))).toEqual([]);
});
