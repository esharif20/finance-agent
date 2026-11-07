---
area: system
status: accepted
---
# One read-only SimpleFIN aggregator is allowed, under a £9 total monthly cap

Supersedes ADR 0012. Importing CSVs by hand is the main reason the Ledger goes stale, so the app may sync from one paid SimpleFIN aggregator. This reverses 0012's rejection of paid aggregators: the aggregator does see the user's transactions, but it already relays them from the Banks under its own regulation, and none of the app's other boundaries change. Egress is allowed to exactly one SimpleFIN host, pinned in config, over HTTPS, read-only by protocol, and only from the `ingest` SimpleFIN client; the access token lives in the Keychain. The aggregator is the only subscription in the app, at most £5 a month (£60 a year). Every paid service shares one cost meter with a hard £9 monthly cap: up to £5 for the aggregator and up to £4 for pay-as-you-go items, split as £1.50 for the cloud model generating synthetic data and judging evaluations, £1 for opt-in gateway conversations (ADR 0011), £1 for the demo domain and £0 for backup, which goes to a local external drive (#95; it was £0.50 for encrypted off-site backup). At a cap, calls fall back to local or deterministic answers, never to an overspend, and a warning fires at 80%. Adding another subscription or egress host, or raising either cap, needs a new ADR and the user's explicit approval.
