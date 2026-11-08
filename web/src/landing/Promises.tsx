const PROMISES = [
  {
    title: "Your data stays on your Mac.",
    body: "Statements, transactions and everything the Assistant learns about you sit in a database on your own computer. There is no account to create and no server of ours holding a copy.",
  },
  {
    title: "The cloud is opt-in, and sees placeholders.",
    body: "The Assistant runs on a local model. If you turn the cloud model on for a conversation, names, amounts and account details are swapped for placeholders before anything leaves, and the real values are filled back in on your Mac. You can read exactly what was sent.",
  },
  {
    title: "It never moves money.",
    body: "Finance Agent reads your accounts and nothing else. There is no way for it, or for anyone using it, to make a payment or a transfer.",
  },
];

export function Promises() {
  return (
    <section id="privacy" className="lp-promises" aria-labelledby="lp-promises-title">
      <div className="lp-wrap">
        <h2 id="lp-promises-title" className="lp-h2">
          Three promises, in plain words.
        </h2>
        <ul className="lp-promise-list">
          {PROMISES.map((p) => (
            <li key={p.title} className="lp-promise">
              <h3 className="lp-h3">{p.title}</h3>
              <p>{p.body}</p>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
