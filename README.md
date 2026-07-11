# Syrudas AI

A self-hosted AI workspace in the spirit of [Odysseus](https://github.com/pewdiepie-archdaemon/odysseus),
built around one idea: **any model backend plugs in through a small provider API**.

- **Chat** — streaming responses, markdown + syntax highlighting, conversation history (SQLite),
  file attachments (drag & drop or 📎: code, text, CSV, JSON, logs, PDFs)
- **Any model** — provider *types* are Python plugins; provider *instances* are configured in the UI.
  The builtin OpenAI-compatible adapter covers Ollama, LM Studio, llama.cpp server, vLLM,
  OpenRouter, and OpenAI itself
- **Agent mode** — the model plans and calls tools: PowerShell (per-call approval gate in the UI),
  file read/write/list (workspace by default, plus folders you grant under
  Settings → Agent file access), web fetch, web search
- **MCP** — register stdio MCP servers in Settings; their tools merge into agent mode
- Local-first: FastAPI + React, SQLite, no telemetry, keys never leave your machine

**Docs:** [Setup guide](docs/SETUP.md) · [Whitepaper](docs/WHITEPAPER.md)
([PDF](docs/Syrudas-AI-Whitepaper.pdf) — regenerate with `scripts\render_whitepaper.py`)

## Quickstart

Requirements: Windows, Python 3.13 (`py` launcher), Node.js 20+, and a model backend
(e.g. [Ollama](https://ollama.com) with a tool-capable model like `llama3.1:8b`).

```powershell
.\setup.ps1     # venv + pip + npm install + frontend build
.\run.ps1       # server only, use in a browser at http://127.0.0.1:8040
```

### Desktop app (one-click exe)

`.\build_exe.ps1` builds **SyrudasAI.exe** (PyInstaller onefile, ~27 MB) into the project
root. Double-click it and Syrudas opens as a native desktop window (WebView2 via
pywebview — built into Windows 11, no browser needed); closing the window stops the
server. If an instance is already running, it just opens a window onto it, and if the
native webview is unavailable it falls back to your default browser. The exe keeps its
state (`data\`, `plugins\`) in the folder it lives in, so you can copy it anywhere for a
fresh portable instance — next to this repo it shares the dev database. Windowed logs go
to `data\syrudas.log`. Dev equivalents: `python desktop.py` (window) or `.\run.ps1`
(browser).

On first run Syrudas auto-detects a running Ollama or LM Studio and configures it as a
provider. To add more: **Settings → Model providers → Add provider**, pick
*OpenAI-compatible*, set the Base URL (e.g. `http://localhost:11434/v1` for Ollama).
Pick a model in the top bar and chat. Toggle **Agent mode** to let the model use tools.

### Shipping a release

`.\build_release.ps1` builds the exe (with version metadata from `APP_VERSION` in
[server/config.py](server/config.py)) and packages `release\SyrudasAI-vX.Y.Z-win64.zip`
containing the exe, an end-user `README.txt`, and the MIT `LICENSE.txt`. To cut a new
version: bump `APP_VERSION` and the numbers in `version_info.txt`, then rerun the script.
The exe is unsigned, so recipients may need to click through SmartScreen once.

## VS Code integration

Two connectors, both talking to the local server:

- **Syrudas AI extension** ([vscode-extension/](vscode-extension)) — a panel with the
  full Syrudas UI inside VS Code, plus right-click **Syrudas: Ask About Selection** on
  any code selection (prefills a chat with the code block). Build with
  `npx @vscode/vsce package` and install the `.vsix` via
  `code --install-extension syrudas-ai-<version>.vsix`.
- **OpenAI-compatible hub at `/v1`** — `GET /v1/models` lists every model from every
  configured provider as `<instance>/<model>`; `POST /v1/chat/completions` (streaming
  and non-streaming) routes to the right backend. Point Continue or any other
  OpenAI-compatible tool at `http://127.0.0.1:8040/v1` (any api key) and manage all
  your backends in one place.

## Writing a provider plugin

Drop a `.py` file into `plugins/` (see `plugins/example_echo.py`), restart, and the new
type appears in Settings. The whole contract:

```python
class MyProvider(ModelProvider):
    type_id = "my_backend"
    display_name = "My Backend"
    config_fields = [ConfigField(key="api_key", label="API key", type="password")]

    async def list_models(self) -> list[ModelInfo]: ...
    async def chat(self, model, messages, tools=None, params=None) -> AsyncIterator[StreamEvent]:
        # translate normalized messages/tools to your wire format, then yield
        # text_delta / tool_call / usage events, ending with done (or error).
```

Messages, tools, and stream events are normalized in [server/schemas.py](server/schemas.py) —
adapters translate at the edge, the rest of the app never knows which backend is talking.

## Layout

```
server/            FastAPI backend
  providers/       plugin contract (base.py), registry, openai_compat adapter
  routes/          REST + streaming chat API (NDJSON over POST /api/chat)
  tools/           builtin agent tools (shell, files, web)
  agent.py         agent loop + approval gate
  mcp_client.py    stdio MCP servers -> agent tools
plugins/           drop-in provider plugins
web/               Vite + React frontend (built to web/dist, served by the backend)
scripts/           smoke tests (run against a live Ollama)
data/              SQLite DB + agent workspace (gitignored)
```

## Notes

- Agent tool calls that run shell commands always pause for approval in the UI.
- File tools are sandboxed to `data/workspace`.
- API keys are stored in the local SQLite DB and masked in API responses.
- Tool calling requires a model that supports it (e.g. `llama3.1:8b`; `gemma3` does not).
