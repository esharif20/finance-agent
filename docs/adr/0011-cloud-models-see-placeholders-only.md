---
area: system
status: accepted
---
# Cloud models see placeholders, never figures or merchant names

Any call to a cloud model goes through the privacy gateway, a module with no database access. Local code computes the result, and the gateway sends only allowlisted fields, with figures, merchants and other personal values replaced by placeholders such as {P1}. The cloud model writes wording using the same placeholders, and the real values are filled in locally after the reply is validated. Cloud use needs explicit opt-in per conversation, every payload is logged for the user to inspect, and an invalid reply falls back to the local model or a deterministic answer. Sending minimised but real figures would give better prose but leak the data the app exists to protect.
