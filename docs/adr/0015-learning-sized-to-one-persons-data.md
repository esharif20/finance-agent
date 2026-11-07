---
area: system
status: accepted
---
# Learning is sized to one person's data: pretrained models for inference, small-data statistics, bandits, no full RL

One user produces a few thousand Transactions a year, a few hundred Corrections and roughly one feedback signal a day. Deep models trained from scratch on this data, such as sequence models of spending, would overfit and lose to seasonal naive. Fine-tuning on real data is already ruled out by ADR 0009.

What is allowed:

- **Pretrained models, locally, for inference only.** For example, Ollama text embeddings of Merchant descriptors for nearest-neighbour categorisation. No weights change. The stored vectors are deletable data, like Corrections.
- **Small-data methods**: calibrated linear or boosted classifiers, Bayesian baselines, ETS, change-point detection, active learning, pairwise preferences and contextual bandits.

Full reinforcement learning (learned policies over long-horizon actions) is out of scope. Rewards are sparse and delayed by months. A simulator of the user would mostly teach its own quirks. And the system has no money-moving actions to learn: no payments, ever, and the Assistant only proposes.

Every learned component needs three things:

1. **A minimum-data gate.** Below the gate, the rules or the baseline stay in charge, and the UI says why.
2. **A baseline it must beat** (`docs/ML_EVALUATIONS.md`).
3. **Shadow mode before promotion.**

Bandit rewards are acceptances and acted-on outcomes, never clicks or engagement. Exposure is capped. Every shown item logs its propensity from day one, so later policies can be evaluated off-policy.
