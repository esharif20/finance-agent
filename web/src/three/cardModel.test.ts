import * as THREE from "three";
import { expect, it } from "vitest";
import { createCardParts, createPaymentCardModel } from "./cardModel";

// #93: each card has a back, the faded mirror of its face, and every GPU resource it adds
// is freed when the scene goes.
const textures = (m: THREE.Material) =>
  Object.values(m).filter((v): v is THREE.Texture => v instanceof THREE.Texture);

function build() {
  const parts = createCardParts(360);
  const front = new THREE.Texture();
  const back = new THREE.Texture();
  const card = createPaymentCardModel(parts, front, true, back);
  return { parts, front, back, card };
}

it("shows the back texture on the far side, so a turned card reads as its face seen through", () => {
  const { card, back } = build();
  const mesh = card.group.getObjectByName("card-back") as THREE.Mesh;
  const m = mesh.material as THREE.MeshPhysicalMaterial;
  expect(m.map).toBe(back);
  expect(m.side).toBe(THREE.BackSide); // seen from behind, the face's image comes out mirrored
  const face = card.group.getObjectByName("card-face") as THREE.Mesh;
  expect(mesh.position.z).toBeLessThan(face.position.z); // behind the body, not on the front
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
