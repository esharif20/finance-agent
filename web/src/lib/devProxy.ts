/** Where `vite` and `vite preview` send /api. The local app's API listens on 8765 and reads the
    real, encrypted ledger, so a demo build (VITE_DEMO=1) must never be pointed there: it puts a
    "synthetic data" banner over real accounts, and `--host` would share them on the LAN. A demo
    build needs an explicit FA_DEMO_API (the `fa demo` or uvicorn demo server). */
export const REAL_PORT = "8765";

export function apiTarget(env: Record<string, string | undefined>): string {
  if (env.VITE_DEMO !== "1") return `http://127.0.0.1:${REAL_PORT}`;
  const target = env.FA_DEMO_API;
  if (!target)
    throw new Error("VITE_DEMO=1: set FA_DEMO_API to the demo server (e.g. http://127.0.0.1:<port> from `fa demo --no-open`)");
  let url: URL;
  try {
    url = new URL(target);
  } catch {
    throw new Error(`FA_DEMO_API is not a URL: ${target}`);
  }
  if (url.port === REAL_PORT)
    throw new Error(`FA_DEMO_API points at port ${REAL_PORT}, the real app's API with your own ledger; use the demo server`);
  return url.origin;
}
