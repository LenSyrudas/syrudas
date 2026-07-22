# Syrudas AI — Setup Guide

This guide covers everything from "I just want to use it" to "I want to hack
on it and build my own releases."

- [1. Quick start (portable app)](#1-quick-start-portable-app)
- [2. Setting up a model backend](#2-setting-up-a-model-backend)
- [3. Configuring providers](#3-configuring-providers)
- [4. Using agent mode](#4-using-agent-mode)
- [5. MCP servers](#5-mcp-servers)
- [6. File attachments](#6-file-attachments)
- [7. Knowledge (local RAG)](#7-knowledge-local-rag)
- [8. Deep research](#8-deep-research)
- [9. Writing editor](#9-writing-editor)
- [10. Blind arena](#10-blind-arena)
- [11. Model cookbook](#11-model-cookbook)
- [12. Appearance & accessibility](#12-appearance--accessibility)
- [13. VS Code integration](#13-vs-code-integration)
- [14. Running from source (developers)](#14-running-from-source-developers)
- [15. Building the exe and releases](#15-building-the-exe-and-releases)
- [16. Privacy & security](#16-privacy--security)
- [17. Troubleshooting](#17-troubleshooting)

---

## 1. Quick start (portable app)

**Requirements:** Windows 10/11. Windows 11 already includes the WebView2
runtime the app window uses.

1. Unzip `SyrudasAI-vX.Y.Z-win64.zip`. It contains a single **`SyrudasAI`**
   folder — move that folder wherever you want to keep it (e.g.
   `C:\SyrudasAI`), not your Downloads folder. The app stores everything
   (`data\`, `plugins\`) inside that folder, so keep it together. There's
   nothing to rename.
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

Two more provider types ship as optional connectors (in the `plugins\` folder):

- **Anthropic (Claude)** — pick the *Anthropic (Claude)* type and paste an API key from
  <https://platform.claude.com/>. Models (Claude Opus, Sonnet, Haiku…) are listed live.
- **Google (Gemini)** — pick the *Google (Gemini)* type and paste an API key from
  <https://aistudio.google.com/>. Only chat-capable Gemini models are listed.

Both support streaming and agent-mode tool calling.

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
- read, list, and write files (writing *outside* the workspace also asks first),
- search the web, and **fetch** web pages (fetches ask first, and private/LAN
  addresses are always refused),
- remember durable facts across conversations and search them,
- search your indexed documents (see [Knowledge](#7-knowledge-local-rag)),
- use any tools from connected MCP servers.

**File access:** by default the file tools only see the agent workspace
(`data\workspace`). To let the agent work on real folders, go to
**Settings → Agent file access** and grant paths (e.g. `D:\projects\myapp`).
The agent can then use absolute paths inside granted folders; everything
else is refused. Remove a grant any time.

**Agent memory:** when you share something worth keeping (a preference, a
project detail, a decision), the agent can save it, and it's shown to the
agent at the start of future agent-mode conversations. Review, add, and
delete memories under **Settings → Agent memory**. Plain chat never sees
them, and nothing is saved silently — each save shows up as a tool card.

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

## 5½. Chat features worth knowing

- **🎭 System prompt / persona** (topbar): sets the assistant's role for the
  current conversation; save frequently-used ones as presets. New chats start
  with whatever the panel holds.
- **🎛 Tuning** (topbar): temperature and max-tokens for your requests; leave
  untouched for backend defaults.
- **↻ Regenerate** (under the last reply) and the **✎ pencil** on your last
  message (hover): retry a weak answer, or pull your message back into the
  composer to fix and resend. If the backend fails mid-regenerate, the old
  reply is restored.
- **⤓ Export** (topbar): download the conversation as Markdown.
- Long conversations are automatically trimmed to fit the model: the system
  prompt and newest messages always survive; the oldest turns fall off first.

## 6. File attachments

In any chat, click **📎** or drag files onto the message box. Supported:
code, text, markdown, CSV, JSON, logs, and **PDFs** (text is extracted).
Binary files are rejected. Very large files are truncated (the chip says
so). Attached files render as collapsible chips in your message — click to
see exactly what the model received.

Note: small local models have small context windows; a large attachment can
exceed what the model can actually ingest. For big documents prefer a
long-context model.

## 7. Knowledge (local RAG)

**Settings → Knowledge.** Index your own files and folders so the agent can
quote from documents far larger than the context window — everything stays on
your machine.

1. **Pick an embedding model.** Choose a provider and an embedding model
   (e.g. Ollama/LM Studio serving `nomic-embed-text`), then **Save & test**.
   Switching the embedding model later clears the index, because vectors from
   different models aren't comparable.
2. **Index a file or folder.** Enter a path (must be the workspace or a
   granted folder — see [Agent file access](#4-using-agent-mode)). Text, code,
   and PDFs are chunked and embedded. Reindex or Remove sources any time; the
   built-in search box lets you sanity-check what will be retrieved.

In agent mode the model uses the read-only `knowledge_search` tool to pull
the most relevant passages before answering. Deep Research (below) searches
the index too.

## 8. Deep research

Type a question and click **🔎 Research** in the composer (new chat only).
Syrudas plans a few web searches, reads the top sources (and your Knowledge
index), and writes a cited Markdown report with a Sources list — streamed
live, and saved as a normal conversation you can export. Fetching happens
without a per-source approval prompt (that's the point of an autonomous
research run), but the same private-address protection applies.

## 9. Writing editor

**✍ Editor** in the sidebar opens a document workspace. Documents autosave
locally. Select text and use **Improve / Shorten / Expand / Fix grammar**,
**Continue** from the cursor, or **✏ Custom** for your own instruction; the
AI suggestion streams into a panel and you **Accept** or **Reject** it. The
editor is locked while a suggestion is pending so an accepted edit always
lands where you selected.

## 10. Blind arena

**⚔ Arena** in the sidebar pits two models against the same prompt with their
names hidden (the columns are randomised). Both answers stream side by side;
you vote for the better one — A, B, tie, or both bad — and the names are then
revealed. Votes build a local win/loss leaderboard so you can find the best
model for your prompts over time.

## 11. Model cookbook

**📖 Cookbook** in the sidebar detects your CPU, RAM, and GPU and rates a
curated list of local models as *fits your GPU / tight / CPU / too big* for
your machine. If **Ollama** is running, click **Download** to pull a model
straight in (with progress); it then appears in the normal model picker.
Filter by capability (chat, tools, code, vision, embedding, reasoning).
Downloading requires Ollama specifically; the recommendations show regardless.

## 12. Appearance & accessibility

**Settings → Appearance** (or the ☀/🌙 toggle in the sidebar):

- **Theme:** System, Light, or Dark. System follows your OS.
- **Colour vision:** Default, Protanopia, Deuteranopia, Tritanopia, or
  Achromatopsia — each remaps status colours to a palette that stays legible
  and distinguishable for that type. Status is never colour-only: every state
  also has an icon and label.

Preferences are saved on this device. The app also respects your OS
*reduced motion* setting.

**Screen reader & keyboard:** every button has an accessible name (icon-only
ones included), and the conversation and document lists are keyboard-focusable
— so the workspace is usable without a mouse or with a screen reader.

**Picks up where you left off:** the view you were in and the conversation you
had open are remembered, so relaunching Syrudas returns you to the same place.

## 13. VS Code integration

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

## 14. Running from source (developers)

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

Run everything with one command — the offline Python suites plus the frontend
unit tests, lint and typecheck. This is exactly what CI runs on every push and
pull request (see [.github/workflows/ci.yml](../.github/workflows/ci.yml)); it
exits non-zero if anything fails:

```powershell
.\run_tests.ps1              # offline suites + frontend unit/lint/build
.\run_tests.ps1 -SkipWeb     # Python only (no Node needed)
.\run_tests.ps1 -Smoke       # also run the live smoke tests (needs a model)
```

Frontend unit tests (Vitest + Testing Library, jsdom — no browser needed) cover
the thread reducer, the saved-conversation rebuild, the clipboard helper and the
sidebar's search/rename behaviour:

```powershell
cd web
npm test          # once
npm run test:watch  # re-run on change
```

The individual offline suites (no network, no model, no GPU needed — they drive
the real code against fakes):

```powershell
.venv\Scripts\python.exe scripts\test_agent_safety.py   # tool gating + sandbox
.venv\Scripts\python.exe scripts\test_host_guard.py     # localhost Host-guard
.venv\Scripts\python.exe scripts\test_agent_memory.py   # memory
.venv\Scripts\python.exe scripts\test_knowledge.py      # retrieval / RAG
.venv\Scripts\python.exe scripts\test_research.py       # deep research pipeline
.venv\Scripts\python.exe scripts\test_documents.py      # writing editor
.venv\Scripts\python.exe scripts\test_arena.py          # blind arena
.venv\Scripts\python.exe scripts\test_cookbook.py       # cookbook + fit ratings
.venv\Scripts\python.exe scripts\test_hardware.py       # hardware detection
.venv\Scripts\python.exe scripts\test_connectors.py     # Anthropic / Gemini
```

Live smoke tests (these need Ollama running with `llama3.1:8b`):

```powershell
.venv\Scripts\python.exe scripts\smoke_provider.py   # provider adapter
.venv\Scripts\python.exe scripts\smoke_chat_api.py   # chat API end-to-end
.venv\Scripts\python.exe scripts\smoke_agent.py      # agent loop + tools
.venv\Scripts\python.exe scripts\smoke_mcp.py        # MCP through the agent
```

Layout reference is in [README.md](../README.md); design rationale in
[WHITEPAPER.md](WHITEPAPER.md).

## 15. Building the exe and releases

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

## 16. Privacy & security

Syrudas is local-first and single-user by design:

- **No telemetry, no account, no cloud.** Outbound connections go only to the
  model backends you configure and to URLs you (or an agent, under the rules
  above) request.
- **Local only.** The server binds to `127.0.0.1` and rejects requests with a
  non-loopback `Host` header, so a web page you're visiting can't quietly
  drive it. Web fetches (agent and research) refuse private/LAN/loopback
  addresses, on every redirect.
- **Consent-gated actions.** Shell commands, web fetches, and file writes
  outside the workspace each require a per-call Approve/Deny. There's no
  "always allow."
- **Keys at rest.** Provider API keys live in `data\syrudas.db` in
  **plaintext** (masked only when sent back to the UI). Anyone with read
  access to your `data\` folder can read them — keep that folder as private as
  the keys themselves. OS-keychain storage is future work.
- **What's in `data\`:** conversations, settings, memories, indexed-document
  text and embeddings, editor documents, and arena results — all local. Delete
  the folder to reset everything.

## 17. Troubleshooting

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
