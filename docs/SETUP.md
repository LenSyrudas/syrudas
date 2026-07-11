# Syrudas AI — Setup Guide

This guide covers everything from "I just want to use it" to "I want to hack
on it and build my own releases."

- [1. Quick start (portable app)](#1-quick-start-portable-app)
- [2. Setting up a model backend](#2-setting-up-a-model-backend)
- [3. Configuring providers](#3-configuring-providers)
- [4. Using agent mode](#4-using-agent-mode)
- [5. MCP servers](#5-mcp-servers)
- [6. File attachments](#6-file-attachments)
- [7. VS Code integration](#7-vs-code-integration)
- [8. Running from source (developers)](#8-running-from-source-developers)
- [9. Building the exe and releases](#9-building-the-exe-and-releases)
- [10. Troubleshooting](#10-troubleshooting)

---

## 1. Quick start (portable app)

**Requirements:** Windows 10/11. Windows 11 already includes the WebView2
runtime the app window uses.

1. Unzip `SyrudasAI-vX.Y.Z-win64.zip` into a folder of its own, e.g.
   `C:\SyrudasAI`. The app stores everything (`data\`, `plugins\`) next to
   the exe, so give it a real home — not your Downloads folder.
2. Double-click **SyrudasAI.exe**.
   - If Windows shows *"Windows protected your PC"*, that is SmartScreen
     being cautious about unsigned apps: click **More info → Run anyway**.
3. A Syrudas window opens. If a local model server (Ollama or LM Studio) is
   already running, Syrudas detects it on first launch and configures it
   automatically — pick a model in the top bar and chat.
4. Closing the window stops the app. Launching the exe again while it's
   already running just opens a new window onto the same instance.

Your data never leaves the folder: conversations and settings are in
`data\syrudas.db`, logs in `data\syrudas.log`.

## 2. Setting up a model backend

Syrudas talks to model servers; it doesn't bundle one. The easiest path:

### Ollama (recommended)

1. Install from <https://ollama.com> and let it run in the background.
2. Pull a model. For agent mode you want one that supports **tool calling**:

   ```powershell
   ollama pull llama3.1:8b
   ```

   (`gemma3` and most embedding models do *not* support tools — fine for
   plain chat, not for agent mode.)

### LM Studio

Start LM Studio's local server (default `http://localhost:1234`). Syrudas
auto-detects it on first run just like Ollama.

### Hosted APIs (OpenRouter, OpenAI, any OpenAI-compatible service)

No install needed — just an API key. See the next section.

## 3. Configuring providers

**Settings → Model providers → Add provider.**

A *provider* is one configured connection. The builtin type is
**OpenAI-compatible**, which covers:

| Backend | Base URL | API key |
|---------|----------|---------|
| Ollama | `http://localhost:11434/v1` | — |
| LM Studio | `http://localhost:1234/v1` | — |
| OpenRouter | `https://openrouter.ai/api/v1` | your key |
| OpenAI | `https://api.openai.com/v1` | your key |
| vLLM / llama.cpp server | wherever you run it | usually — |

Use **Test** on any provider card to verify the connection. Models from all
providers appear in the model picker in the top bar. Keys are stored locally
and shown masked afterwards.

Want a backend that doesn't speak the OpenAI dialect? Drop a provider plugin
(a single Python file subclassing `ModelProvider`) into the `plugins\`
folder next to the exe and restart — see `plugins/example_echo.py` in the
source tree for the template.

## 4. Using agent mode

Toggle **Agent mode** in the top bar (pick a tool-capable model first).
The model can then:

- run PowerShell commands — **every command pauses for your Approve/Deny**,
- read/write/list files,
- fetch web pages and search the web,
- use any tools from connected MCP servers.

**File access:** by default the file tools only see the agent workspace
(`data\workspace`). To let the agent work on real folders, go to
**Settings → Agent file access** and grant paths (e.g. `D:\projects\myapp`).
The agent can then use absolute paths inside granted folders; everything
else is refused. Remove a grant any time.

## 5. MCP servers

**Settings → MCP servers → Add server.** Give it a name and the full command
line of a stdio MCP server. Example — filesystem access via the reference
server (requires Node.js):

```
npx -y @modelcontextprotocol/server-filesystem D:\some\folder
```

Enabled servers connect when an agent run starts; their tools appear to the
model namespaced by server name (e.g. `filesystem_list_directory`). The
first `npx` run downloads the package, so the first agent start after adding
one can take a minute.

## 6. File attachments

In any chat, click **📎** or drag files onto the message box. Supported:
code, text, markdown, CSV, JSON, logs, and **PDFs** (text is extracted).
Binary files are rejected. Very large files are truncated (the chip says
so). Attached files render as collapsible chips in your message — click to
see exactly what the model received.

Note: small local models have small context windows; a large attachment can
exceed what the model can actually ingest. For big documents prefer a
long-context model.

## 7. VS Code integration

Two independent connectors (Syrudas must be running for both):

### The Syrudas extension

Install `vscode-extension/syrudas-ai-<version>.vsix`:

```powershell
code --install-extension syrudas-ai-0.2.0.vsix          # regular VS Code
code-insiders --install-extension syrudas-ai-0.2.0.vsix # Insiders
```

Then:
- **Ctrl+Shift+P → "Syrudas: Open Panel"** — the workspace in an editor panel.
- Select code → right-click → **"Syrudas: Ask About Selection"** — opens a
  chat prefilled with the selection (file, line, syntax fence).
- Setting `syrudas.url` changes the server address (default
  `http://127.0.0.1:8040`).

### The /v1 model hub (Continue and friends)

Syrudas exposes an OpenAI-compatible API at `http://127.0.0.1:8040/v1`.
Every model from every provider you configured is available as
`<provider>/<model>` (see `GET /v1/models`). For the Continue extension, add
to `~/.continue/config.yaml`:

```yaml
models:
  - name: Syrudas Autodetect
    provider: openai
    model: AUTODETECT
    apiBase: http://127.0.0.1:8040/v1/
    apiKey: syrudas   # any value; Syrudas doesn't check it
```

Any other OpenAI-compatible tool works the same way — point it at the base
URL and go.

## 8. Running from source (developers)

**Prerequisites:** Windows, Python 3.13 (`py` launcher), Node.js 20+.

```powershell
git clone <repo> syrudas
cd syrudas
.\setup.ps1     # venv + pip install + npm install + frontend build
.\run.ps1       # server only -> use in a browser at http://127.0.0.1:8040
```

Alternatives:

```powershell
.venv\Scripts\python.exe desktop.py                 # native desktop window
.venv\Scripts\python.exe -m uvicorn server.main:app --port 8040 --reload  # API dev
cd web; npm run dev                                 # frontend HMR (proxies /api to :8040)
```

Smoke tests (need Ollama running with `llama3.1:8b`):

```powershell
.venv\Scripts\python.exe scripts\smoke_provider.py   # provider adapter
.venv\Scripts\python.exe scripts\smoke_chat_api.py   # chat API end-to-end
.venv\Scripts\python.exe scripts\smoke_agent.py      # agent loop + tools
.venv\Scripts\python.exe scripts\smoke_mcp.py        # MCP through the agent
```

Layout reference is in [README.md](../README.md); design rationale in
[WHITEPAPER.md](WHITEPAPER.md).

## 9. Building the exe and releases

```powershell
.\build_exe.ps1       # -> SyrudasAI.exe in the project root
.\build_release.ps1   # -> release\SyrudasAI-vX.Y.Z-win64.zip (exe + README + license)
```

To cut a new version: bump `APP_VERSION` in `server\config.py` **and** the
matching numbers in `version_info.txt`, then run `build_release.ps1`.

Notes:
- The exe is unsigned; recipients will click through SmartScreen once.
- The build fails with *Access is denied* if a built exe is currently
  running (Windows locks running executables) — close Syrudas first.

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Window doesn't open, browser tab opens instead | WebView2 runtime missing (rare off Windows 11) — install "WebView2 Runtime" from Microsoft, or just use the browser fallback. |
| "Windows protected your PC" | SmartScreen on an unsigned exe: **More info → Run anyway**. |
| Model picker says "no models" | Backend not running or wrong Base URL — use **Test** on the provider card. For Ollama confirm `ollama list` works. |
| Agent replies with the tool call as text instead of using it | The model doesn't support tool calling (e.g. `gemma3`) — switch to `llama3.1:8b` or similar. Low temperature helps small models call tools reliably. |
| Port 8040 already in use | Another Syrudas instance is running (check the tray/taskbar), or another app owns the port. A second launch of the exe intentionally opens a window onto the existing instance. |
| Attachment rejected as binary | Only text-based formats and PDFs are supported. Scanned-image PDFs with no text layer are rejected too. |
| `file_read` says "Path not in an allowed folder" | Grant the folder under **Settings → Agent file access**, or use a path inside `data\workspace`. |
| Where are the logs? | `data\syrudas.log` next to the exe (desktop app); the console when running `run.ps1`. |
| First agent run after adding an MCP server is slow | `npx` downloads the server package on first use. |
