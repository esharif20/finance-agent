import { Hero } from "./Hero";
import { Promises } from "./Promises";
import "./landing.css";

const GITHUB = "https://github.com/esharif20/finance-agent";

const FEATURES = [
  {
    title: "Import",
    body: "Bring in the CSV exports your bank already gives you, or connect a read-only feed. Duplicates are caught and transfers between your own accounts are paired, so nothing is counted twice.",
  },
  {
    title: "Review Queue",
    body: "Every transaction gets a category. The ones it is unsure about wait in one short queue for you to confirm, and each answer teaches it for next time.",
  },
  {
    title: "Insights",
    body: "A weekly briefing of what changed: a subscription that went up, a category running ahead of last month, a forecast for the end of the month.",
  },
  {
    title: "An Assistant that never invents a figure",
    body: "Ask anything about your spending. Every number in its reply comes from a calculation it ran on your ledger, and it says so when it cannot answer.",
  },
];

function Mark() {
  return (
    <span className="lp-mark" aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  );
}

export default function Landing() {
  return (
    <div className="lp">
      <a className="lp-skip" href="#lp-main">
        Skip to content
      </a>
      <header className="lp-nav">
        <a className="lp-brand" href="/">
          <Mark />
          Finance Agent
        </a>
        <nav aria-label="Sections" className="lp-nav-links">
          <a href="#what">What it does</a>
          <a href="#privacy">Privacy</a>
          <a href={GITHUB}>GitHub</a>
        </nav>
        <a className="lp-pill lp-pill-primary lp-pill-small" href="/app">
          Open the demo
        </a>
      </header>
      <main id="lp-main">
        <Hero />
        <section id="what" className="lp-what" aria-labelledby="lp-what-title">
          <div className="lp-wrap lp-what-grid">
            <h2 id="lp-what-title" className="lp-h2">
              What it does
            </h2>
            <dl className="lp-features">
              {FEATURES.map((f) => (
                <div key={f.title} className="lp-feature">
                  <dt className="lp-h3">{f.title}</dt>
                  <dd>{f.body}</dd>
                </div>
              ))}
            </dl>
          </div>
        </section>
        <Promises />
        <section className="lp-open" aria-labelledby="lp-open-title">
          <div className="lp-wrap">
            <h2 id="lp-open-title" className="lp-h2 lp-open-title">
              Try it on a made‑up ledger.
            </h2>
            <p className="lp-open-text">
              The demo runs on synthetic accounts and transactions, made for it. No bank is connected and nothing you do there
              is kept.
            </p>
            <a className="lp-pill lp-pill-primary lp-pill-large" href="/app">
              Open the demo
            </a>
          </div>
        </section>
      </main>
      <footer className="lp-foot">
        <div className="lp-wrap">
          <p className="lp-foot-name" aria-hidden="true">
            Finance Agent
          </p>
          <div className="lp-foot-row">
            <p>A personal finance app that runs on your Mac. Open source.</p>
            <a href={GITHUB}>Source on GitHub</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
