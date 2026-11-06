---
area: backend
status: accepted
---
# Categorisation runs Corrections, then rules, then ML; ML must earn its place

A Transaction's Category comes from the first confident source in this order: the user's Corrections for its Merchant, deterministic rules, then a local ML classifier. Anything below the confidence threshold goes to the Review Queue instead of being guessed, and every assignment records its source. The ML classifier ships only if it beats Corrections plus rules alone on a fixed synthetic evaluation set (macro F1 and calibration). A reader might expect ML first; we chose this order because a few dozen repeat Merchants cover most spending, and rules are explainable and testable.
