---
Status: accepted — revisit if API cost becomes prohibitive
---

# Use the hosted Claude CLI as the AI backend, not a local model

The Navigator calls out to the hosted `claude` CLI for review generation rather than a local model runtime (Ollama, llama.cpp, etc.), even though the project is framed around a "local AI" pair-programming partner. A local model would remove the network/cost dependency and keep code fully offline, but at a real cost to review quality and setup friction for the Driver.

We're accepting the cloud dependency and its per-call cost for now. This is an explicit, reversible bet: if API cost becomes prohibitive for regular use, swap the backend for a local model.
