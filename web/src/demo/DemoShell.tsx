import { App } from "@/App";
import { DemoBanner } from "./DemoBanner";

/** `/app` in the demo build: the local app over the visitor's synthetic ledger, under the banner. */
export function DemoShell() {
  return (
    <App>
      <DemoBanner />
    </App>
  );
}
