---
area: backend
status: accepted
---
# The Assistant is one LangGraph graph, not a multi-agent system

The Assistant runs as one LangGraph graph: a router sends each request to a specialist sub-graph (Ask, Plan, Memory, Correct), all sub-graphs pass through one grounding check, and any Proposal pauses the graph until the user confirms. LangGraph earns its place through that pause and resume; a plain loop would need it built by hand. We rejected a multi-agent design because each agent hand-off is another chance for a small local model to pick the wrong tool, and because it is harder to evaluate and easier to steer by injected text. The Engine does not use LangGraph. Graph checkpoints live in the encrypted database, and LangSmith and all external tracing are disabled, enforced by a CI test that allows no network egress except local Ollama.
