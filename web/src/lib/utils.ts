import { type ClassValue, clsx } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

// The type scale in index.css (`--text-*`) adds font sizes tailwind-merge doesn't know;
// without this it reads `text-label` as a colour and drops it next to `text-muted-foreground`.
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [{ text: ["hero", "figure", "title", "heading", "row", "label", "meta"] }],
    },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
