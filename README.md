# Syrudas AI

A self-hosted AI workspace in the spirit of [Odysseus](https://github.com/pewdiepie-archdaemon/odysseus),
built around one idea: **any model backend plugs in through a small provider API**.

- **Chat** — streaming responses, markdown + syntax highlighting, conversation history (SQLite)
- **Any model** — provider *types* are Python plugins; provider *instances* are configured in the UI.
  The builtin OpenAI-compatible adapter covers Ollama, LM Studio, llama.cpp server, vLLM,
  OpenRouter, and OpenAI itself
- **Agent mode** — the model plans and calls tools: PowerShell (per-call approval gate in the UI),
  sandboxed file read/write/list, web fetch, web search
- **MCP** — register stdio MCP servers in Settings; their tools merge into agent mode
- Local-first: FastAPI + React, SQLite, no telemetry, keys never leave your machine

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

Then in the UI: **Settings → Model providers → Add provider**, pick *OpenAI-compatible*,
set Base URL to `http://localhost:11434/v1` (Ollama). Pick a model in the top bar and chat.
Toggle **Agent mode** to let the model use tools.

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
