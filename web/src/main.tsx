import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { DemoShell } from "./demo/DemoShell";
import "./index.css";

export type View = "app" | "landing" | "architecture";

/** First path segment to view. The local build has the app alone at `/` (hash routes inside it);
 * the demo build (`VITE_DEMO=1`) puts the landing page at `/`, the app at `/app` and the
 * architecture page at `/architecture`. */
export const routeTable = (demo: boolean): Record<string, View> =>
  demo ? { "/": "landing", "/app": "app", "/architecture": "architecture" } : { "/": "app" };

export function viewFor(pathname: string, demo: boolean): View {
  const table = routeTable(demo);
  return table[`/${pathname.split("/")[1] ?? ""}`] ?? table["/"] ?? "app";
}

const DEMO = import.meta.env.VITE_DEMO === "1";
// A constant false in the local build, so the bundler drops the import and emits no landing chunk.
const Landing = DEMO ? lazy(() => import("./landing/Landing")) : null;
const Architecture = DEMO ? lazy(() => import("./architecture/Architecture")) : null;
const view = viewFor(window.location.pathname, DEMO);

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      {Landing && view === "landing" ? (
        <Suspense fallback={null}>
          <Landing />
        </Suspense>
      ) : Architecture && view === "architecture" ? (
        <Suspense fallback={null}>
          <Architecture />
        </Suspense>
      ) : (
        DEMO ? <DemoShell /> : <App />
      )}
    </StrictMode>,
  );
}
