# GitHub Copilot instructions

Read [AGENTS.md](../AGENTS.md) first. It is the canonical project guide for AI
assistants. In short:

- Local-first Flask + embedded ChromaDB + vanilla JS; no build step.
- Never commit `data/`, `chroma_db/`, `.env`, or personal portfolio exports.
- Tests use temporary storage and never the network.
- Deletes are tombstoned; stale writes must not resurrect a holding.
- Run `python -m pytest` and `python -m ruff check .` before finishing.
