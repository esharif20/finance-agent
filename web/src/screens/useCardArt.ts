import { useEffect, useState } from "react";
import { loadArt } from "@/lib/cardArt";
import { usePrivacy } from "@/lib/privacy";

const NO_ART: Record<string, HTMLCanvasElement> = {};

/** The user's own card art by key, from the local folder through the API; a key without art
    is left out, so its card keeps the generic face. A card image can carry a name or number:
    privacy mode shows generic faces only. */
export function useCardArt(cards: { art: string | null }[]): Record<string, HTMLCanvasElement> {
  const [loaded, setArt] = useState<Record<string, HTMLCanvasElement>>({});
  const { hidden } = usePrivacy();
  useEffect(() => {
    let live = true;
    for (const key of new Set(cards.flatMap((f) => (f.art ? [f.art] : []))))
      void loadArt(key).then((c) => {
        if (live && c) setArt((a) => ({ ...a, [key]: c }));
      });
    return () => {
      live = false;
    };
  }, [cards]);
  return hidden ? NO_ART : loaded;
}
