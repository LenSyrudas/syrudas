# Syrudas AI for VS Code

Brings your local [Syrudas AI](https://github.com/) workspace into VS Code.

- **Syrudas: Open Panel** — the full Syrudas chat UI in an editor panel.
- **Syrudas: Ask About Selection** (right-click on selected code) — opens the
  panel with the selection prefilled as a code block; type your question and send.
- Works with the Syrudas desktop app or `run.ps1` server on `http://127.0.0.1:8040`
  (change via the `syrudas.url` setting).

Tip: Syrudas also exposes an OpenAI-compatible API at `http://127.0.0.1:8040/v1`,
so extensions like Continue can use every model you've configured in Syrudas.
