import { type MouseEvent, useCallback, useState } from "react";
import { Money } from "@/components/Money";
import { Ask } from "./Ask";
import { Field, Magnetic, Material, Rise, Roll, useStill } from "./fx";
import { Hero } from "./Hero";
import { HowItWorks } from "./HowItWorks";
import { Learns } from "./Learns";
import { Opener } from "./Opener";
import { Preloader } from "./Preloader";
import { Promises } from "./Promises";
import { Story } from "./story/Story";
import { Terminal } from "./terminal/Terminal";
import { OVERVIEW } from "./story/story.fixture";
import "./landing.css";
import "./motion.css";

const GITHUB = "https://github.com/esharif20/finance-agent";
const NAME = "Finance Agent";

const FEATURES = {
  import:
    "Bring in the CSV exports your bank already gives you, or connect a read-only feed. Duplicates are caught and transfers between your own accounts are paired, so nothing is counted twice.",
  review:
    "Every transaction gets a category. The ones it is unsure about wait in one short queue for you to confirm, and each answer teaches it for next time.",
  insights: [
    "A subscription that went up",
    "A category running ahead of last month",
    "A forecast for the end of the month",
    "All in one weekly briefing",
  ],
  assistant:
    "Ask anything about your spending. Every number in its reply comes from a calculation it ran on your ledger, and it says so when it cannot answer.",
};

function Mark() {
  return (
    <span className="lp-mark" aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  );
}

function seenIntro() {
  try {
    return sessionStorage.getItem("lp-intro") === "1";
  } catch {
    return true;
  }
}

export default function Landing() {
  const still = useStill();
  // the tally plays once a session; a returning visitor goes straight to the hero
  const [pre, setPre] = useState(() => !seenIntro());
  const [play, setPlay] = useState(!pre);
  const go = () => {
    setPlay(true);
    try {
      sessionStorage.setItem("lp-intro", "1");
    } catch {
      // private mode: the tally just plays again next time
    }
  };
  // every "Open the demo" plays the opener, which makes the visitor's ledger before the app loads
  const [opening, setOpening] = useState(false);
  const gone = useCallback(() => setOpening(false), []);
  const open = (e: MouseEvent<HTMLDivElement>) => {
    const a = (e.target as Element).closest?.('a[href="/app"]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    setOpening(true);
  };
  return (
    <div className="lp" onClick={open}>
      {pre && <Preloader still={still} onGo={go} onDone={() => setPre(false)} />}
      {opening && <Opener still={still} onGone={gone} />}
      <a className="lp-skip" href="#lp-main">
        Skip to content
      </a>
      <header className="lp-nav">
        <a className="lp-brand" href="/">
          <Mark />
          {NAME}
        </a>
        <nav aria-label="Sections" className="lp-nav-links">
          <a href="#what">
            <Roll text="What it does" />
          </a>
          <a href="#how">
            <Roll text="How it works" />
          </a>
          <a href="#privacy">
            <Roll text="Privacy" />
          </a>
          <a href="/architecture">
            <Roll text="Architecture" />
          </a>
          <a href={GITHUB}>
            <Roll text="GitHub" />
          </a>
        </nav>
        <Magnetic href="/app" className="lp-pill lp-pill-primary lp-pill-small">
          <Roll text="Open the demo" />
        </Magnetic>
      </header>
      <main id="lp-main">
        <Hero delay={0.05} play={play} />
        <Ask />
        <Story />
        <HowItWorks />
        <Learns />
        <Terminal />
        <section id="what" className="lp-what" aria-labelledby="lp-what-title">
          <Field tone="paper" />
          <div className="lp-wrap lp-mosaic">
            <Rise still={still} className="lp-tile lp-tile-head">
              <h2 id="lp-what-title" className="lp-h2">
                What it does
              </h2>
            </Rise>
            <Rise still={still} delay={0.06} className="lp-slot lp-tile-import">
              <Material className="lp-tile">
                <h3 className="lp-h3">Import</h3>
                <p className="lp-tile-big">{FEATURES.import}</p>
              </Material>
            </Rise>
            <Rise still={still} delay={0.12} className="lp-slot lp-tile-review">
              <Material className="lp-tile">
                <h3 className="lp-h3">Review Queue</h3>
                <p>{FEATURES.review}</p>
              </Material>
            </Rise>
            <Rise still={still} delay={0.18} className="lp-slot lp-tile-figure">
              <Material className="lp-tile">
                <h3 className="lp-h3">Overview</h3>
                <p className="lp-figure">
                  <Money value={OVERVIEW.spent} />
                </p>
                <p>spent in {OVERVIEW.month} on the made-up ledger, added up from its transactions</p>
              </Material>
            </Rise>
            <Rise still={still} delay={0.24} className="lp-slot lp-tile-assistant">
              <Material className="lp-tile">
                <h3 className="lp-h3">An Assistant that never invents a figure</h3>
                <p className="lp-tile-big">{FEATURES.assistant}</p>
              </Material>
            </Rise>
            <Rise still={still} delay={0.3} className="lp-slot lp-tile-insights">
              <Material className="lp-tile">
                <h3 className="lp-h3">Insights</h3>
                <ul>
                  {FEATURES.insights.map((l) => (
                    <li key={l}>{l}</li>
                  ))}
                </ul>
              </Material>
            </Rise>
          </div>
        </section>
        <Promises />
        <section className="lp-open" aria-labelledby="lp-open-title">
          <Field tone="orange" />
          <div className="lp-wrap">
            <h2 id="lp-open-title" className="lp-h2 lp-open-title">
              Try it on a<br />
              made-up ledger.
            </h2>
            <div className="lp-open-row">
              <p className="lp-open-text">
                The demo runs on synthetic accounts and transactions, made for it. No bank is connected and nothing you do there
                is kept.
              </p>
              <Magnetic href="/app" className="lp-pill lp-pill-primary lp-pill-large">
                <Roll text="Open the demo" />
              </Magnetic>
            </div>
          </div>
        </section>
      </main>
      <footer className="lp-foot">
        <Field tone="night" />
        <div className="lp-wrap">
          <div className="lp-foot-row">
            <p>A personal finance app that runs on your Mac. Open source.</p>
            <span className="lp-foot-links">
              <a href="/architecture">
                <Roll text="Architecture" />
              </a>
              <a href={GITHUB}>
                <Roll text="Source on GitHub" />
              </a>
            </span>
          </div>
          <p className="lp-foot-name" aria-hidden="true">
            {NAME}
          </p>
        </div>
      </footer>
    </div>
  );
}
