---
area: system
status: superseded by ADR 0013
---
# Paid services run under a hard £7 monthly cap, spent mostly on synthetic data

The project spends about £5–7 a month. Roughly £2 goes to a cloud model generating synthetic datasets and judging evaluations (no privacy cost, and it improves every model), £2 to opt-in gateway conversations (ADR 0011), £1 to the demo domain and £0 to backup (local external drive, #95; it was under £0.50 for encrypted off-site backup). Every paid call goes through one cost meter with a hard £7 monthly cap: at the cap, calls fall back to local or deterministic answers, never to an overspend; a warning fires at 80%. Paid merchant-enrichment APIs and paid aggregators are rejected because they would see the user's data. The cloud model is chosen by benchmark against the synthetic golden set, with prices checked when that milestone starts.
