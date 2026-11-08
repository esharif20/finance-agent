import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { configDefaults, defineConfig } from "vitest/config";
import { apiTarget } from "./src/lib/devProxy";

export default defineConfig(({ command }) => {
  // `pnpm dev` talks to `uv run python -m finance_agent.api`; a demo build (VITE_DEMO=1) must name
  // its demo server in FA_DEMO_API and can never reach the real app's port (src/lib/devProxy.ts).
  // Only serving needs a proxy (`vite` and `vite preview` are both "serve"); a build has none.
  const proxy = command === "serve" && !process.env.VITEST ? { "/api": apiTarget(process.env) } : undefined;
  return {
    plugins: [react(), tailwindcss()],
    resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
    server: { proxy },
    preview: { proxy },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test-setup.ts"],
      css: false,
      exclude: [...configDefaults.exclude, "smoke/**"], // WebKit smokes, run with node
    },
  };
});
