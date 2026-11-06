---
area: system
status: accepted
---
# Own data only; the public demo runs on synthetic data

The app holds one person's real bank data, on their own machine. A public demo will be deployed, but it runs only on synthetic data and never connects to a real bank. Serving other people's bank data would need FCA registration as an account information service provider (or listing as an agent of a regulated aggregator), indemnity insurance and UK GDPR controller duties, which is out of proportion for a solo project. The code keeps multi-user seams (a user id on every row, queries scoped through one data-access layer, migratable SQL) so a hosted multi-user version stays possible, but that move needs its own ADR first.
