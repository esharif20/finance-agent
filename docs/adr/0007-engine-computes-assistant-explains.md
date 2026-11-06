---
area: backend
status: accepted
---
# The Intelligence Engine computes; the Assistant only explains

The system is split in two. The Intelligence Engine is deterministic Python with no LLM: detectors, forecasts, preference models, ranking and the allocation optimiser. It produces Insights and Allocation Plans. The Assistant is the LLM layer: it interprets requests, calls read-only tools that return Engine output, and explains. Every figure in an Assistant reply must match a tool result from that turn (the grounding check), otherwise the reply is regenerated once and then replaced by a deterministic fallback. Letting the LLM reason about money directly would be simpler, but makes figures unverifiable.
