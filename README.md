# Syrudas AI

## ⬇ [**Download Syrudas AI for Windows**](https://github.com/LenSyrudas/syrudas/releases/latest)

Get `SyrudasAI-vX.Y.Z-win64.zip` from that page. Unzip it, and double-click
`SyrudasAI.exe` — that is the whole install.

> **Don't use the green "Code" button, and don't take "Source code (zip)".** Those give
> you the source code, which has no application in it and will not run. If you downloaded
> something called `syrudas-master.zip` and nothing happened, that is why — go to
> [Releases](https://github.com/LenSyrudas/syrudas/releases/latest) instead.

Windows will say *"Windows protected your PC"* the first time, because the app isn't
code-signed. Click **More info → Run anyway**. Everything below this line is for people
who want to read the code or build it themselves.

---

[![CI](https://github.com/LenSyrudas/syrudas/actions/workflows/ci.yml/badge.svg)](https://github.com/LenSyrudas/syrudas/actions/workflows/ci.yml)

A local-first AI workspace for Windows, built on one bet: **the model is the most
replaceable part of this stack, so it should be the easiest piece to swap.** Every
backend — local weights or a hosted frontier API — reaches the application through one
small provider contract, and nothing above that contract knows which model is answering.
Conversations, keys, documents, memory, and the agent runtime all sit above that seam
and outlive whatever is behind it.

Everything runs on your machine: FastAPI + React, one SQLite file, no account, no
telemetry, no cloud component. The server binds to loopback and refuses anything else.

**Docs:** [Setup guide](docs/SETUP.md) · [Whitepaper](docs/WHITEPAPER.md)
([PDF](docs/Syrudas-AI-Whitepaper.pdf) — regenerate with `scripts\render_whitepaper.py`)

## What it does

- **Chat** — streaming replies, markdown with syntax highlighting and per-block copy,
  SQLite history with search and rename, file attachments (code, text, CSV, JSON, logs,
  PDFs), regenerate and edit, per-conversation system prompts with presets,
  temperature/max-token controls, per-reply token counts, markdown export
- **Any model** — provider *types* are Python plugins; provider *instances* are
  configured in the UI. The builtin OpenAI-compatible adapter covers Ollama, LM Studio,
  llama.cpp server, vLLM, OpenRouter, and OpenAI itself; drop-in connectors for
  **Anthropic (Claude)** and **Google (Gemini)** ship in [plugins/](plugins)
- **Agent mode** — the model plans and calls tools: PowerShell (per-call approval),
  file read/write/list (workspace by default, plus folders you grant), web fetch and
  search, and **persistent memory** — durable facts carried across conversations,
  reviewable and deletable in Settings
- **Knowledge (local RAG)** — index files and folders into a local embedding index; the
  agent's `knowledge_search` tool quotes from them, so you can work with far more text
  than fits a context window. Needs any provider serving an embedding model
  (e.g. `nomic-embed-text`); vectors live in the same SQLite file
- **Deep Research** — a fixed plan → search → read → synthesize pipeline (deterministic
  stages, not an agent loop, because that is what small local models can actually do
  reliably) producing a cited Markdown report as a normal conversation
- **Writing editor** — a document workspace with AI edits: Improve / Shorten / Expand /
  Fix grammar on a selection, Continue from the cursor, or a custom instruction.
  Suggestions stream in and you accept or reject; documents autosave
- **Blind arena** — two models answer the same prompt with names hidden and columns
  randomized; you vote, and votes build a local leaderboard
- **Model cookbook** — detects your CPU/RAM/GPU, rates a curated set of local models as
  fits / tight / CPU / too big, and pulls the ones you pick into Ollama. Strictly
  additive: pulled models appear in the ordinary model picker
- **MCP** — register stdio MCP servers in Settings; their tools merge into agent mode,
  gated per call and unable to shadow a builtin's name
- **OpenAI-compatible hub** — every backend you configure here is available to any tool
  that speaks that dialect, at `/v1`
- **Themes & accessibility** — light / dark / system, independently of colour-vision
  modes (protanopia, deuteranopia, tritanopia, achromatopsia), plus `prefers-reduced-motion`.
  Status is never carried by colour alone — every state also has an icon and a label — and
  the lists are keyboard-navigable with hover-revealed actions kept focusable. Keyboard
  operability is solid; screen-reader announcement of streamed replies is not there yet

Smaller things that matter in daily use: each conversation **remembers the provider and
model it used** and restores them when reopened, so a reply never silently comes from a
different model than the rest of the thread. History is trimmed to a budget derived from
the selected model's actual context window rather than one number for everything. The
open view and conversation are restored on relaunch.

## What it won't do quietly

A local tool that runs commands and writes files earns trust by being explicit about
failure, not by looking smooth. The things below are load-bearing, not polish:

- **Risky tool calls stop and ask, every time.** Shell, web fetch, file writes outside
  the workspace, and every MCP tool are gated per call, with the arguments shown.
  Approvals are single-shot and there is deliberately no "always allow." A denial comes
  back to the model as an ordinary tool result, so it redirects rather than crashes.
- **Failed and denied tool calls are shown as failures.** They are not rendered as
  successes with empty output, and `/v1` returns an error status when a backend fails
  instead of a 200 with a truncated stream.
- **Tool output is fenced as data.** Fetched pages and retrieved passages arrive inside
  explicit delimiters with counterfeit markers stripped, and the system prompt says
  fenced content is information, never instructions.
- **`file_write` refuses writes that would destroy data** — content still carrying a
  truncation marker, or a write-back shorter than the truncated read it came from.
- **Paths are re-resolved before every check**, so a symlink or junction cannot step
  outside a granted root — for the file tools, for Knowledge indexing, and for the
  static routes serving the app's own origin.
- **The agent's exit paths are closed.** Every announced tool call gets a recorded
  result on every ending — finished, provider error, step ceiling, denial, cancellation
  — and reconstruction repairs anything a hard kill still managed to break.
- **First run tells the truth.** Detection that fails says so rather than presenting an
  empty list as a finding, on odd hardware and from the wrong folder alike.

Each of these has an offline suite that fails the branch that breaks it. See
[Whitepaper §15](docs/WHITEPAPER.md) for the full threat model, and §19 for what is
still weak — written on purpose and in detail.

## Quickstart

Requirements: Windows, Python 3.13 (`py` launcher), Node.js 20+, and a model backend
(e.g. [Ollama](https://ollama.com) with a tool-capable model like `llama3.1:8b`).

```powershell
.\setup.ps1     # venv + pip + npm install + frontend build
.\run.ps1       # server only, use in a browser at http://127.0.0.1:8040
.\run_tests.ps1 # offline suites + frontend unit tests/lint/typecheck (what CI runs)
```

`run_tests.ps1` runs the 22 offline suites — real code paths against fake providers and
embedders, no network, no GPU, seconds to finish. Add `-Smoke` to also run the suites
that need a live model backend. `scripts\eval_agent.py` scores agent *behaviour* against
a real model (which tools, in what order, how many steps) and is kept out of the default
run because it costs model time.

On first run Syrudas auto-detects a running Ollama or LM Studio and configures it. To add
more: **Settings → Model providers → Add provider**, pick *OpenAI-compatible*, set the
Base URL (e.g. `http://localhost:11434/v1` for Ollama). Pick a model in the top bar and
chat. Toggle **Agent mode** to let the model use tools.

### Desktop app (one-click exe)

`.\tools\build_exe.ps1` builds **SyrudasAI.exe** (PyInstaller onefile) into the project root.
Double-click it and Syrudas opens as a native window (WebView2 via pywebview — built into
Windows 11, no browser needed); closing the window stops the server. A second launch
opens a window onto the running instance, and if the native webview is unavailable it
falls back to your default browser. The exe keeps its state (`data\`, `plugins\`) beside
itself, so copying it anywhere gives a fresh portable install — next to this repo it
shares the dev database. Logs go to `data\syrudas.log`. Dev equivalents:
`python desktop.py` (window) or `.\run.ps1` (browser).

## Use your models from other tools

Syrudas is an **OpenAI-compatible hub at `/v1`**, so every backend you configure here is
available to anything that speaks that dialect — no per-tool API keys, no duplicate
configuration. `GET /v1/models` lists every model from every configured provider as
`<instance>/<model>`; `POST /v1/chat/completions` (streaming and non-streaming) routes to
the right backend, preserving tool calls and usage. These calls are stateless and never
touch conversation history.

For VS Code, point [Continue](https://continue.dev) at it — you get inline completions
and edits applied in the editor, which a chat window cannot do:

```yaml
# ~/.continue/config.yaml
models:
  - name: Syrudas
    provider: openai
    apiBase: http://127.0.0.1:8040/v1
    apiKey: unused
    model: ollama-local/llama3.1:8b
```

The same address works for aider, scripts, or any other OpenAI-compatible client.

## Writing a provider plugin

Drop a `.py` file into `plugins/` (see `plugins/example_echo.py`), restart, and the new
type appears in Settings — including in the *packaged* exe, with no rebuild. The whole
contract:

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

`embed()` and `check()` are optional; implementing `embed()` makes the provider eligible
to power Knowledge. Messages, tools, and stream events are normalized in
[server/schemas.py](server/schemas.py) — adapters translate at the edge, and the rest of
the app never learns which backend is talking.

## Layout

```
server/            FastAPI backend
  providers/       plugin contract (base.py), registry, openai_compat adapter
  routes/          REST + streaming API (NDJSON over POST /api/chat), /v1 hub
  tools/           builtin agent tools (shell, files, web, memory, knowledge)
  agent.py         agent loop, approval gate, exit contract
  chat.py          history assembly, tool-output fencing, context trimming
  knowledge.py     indexing + embedding retrieval
  research.py      deep-research pipeline
  cookbook.py      model catalog + Ollama pulls
  hardware.py      CPU/RAM/GPU detection
  runs.py          per-conversation stream coordination
  security.py      loopback host guard
  db.py            SQLite schema + migrations
  onboarding.py    first-run backend detection
  mcp_client.py    stdio MCP servers -> agent tools
plugins/           drop-in provider plugins (Anthropic, Gemini, example)
web/               Vite + React frontend (built to web/dist, served by the backend)
scripts/           offline test suites (test_*.py), smoke suites needing a live
                   backend (smoke_*.py), and eval_agent.py
tools/             packaging: build_exe, build_release, verify_release, plus the
                   exe's icon and version resource. Not server/tools, which is
                   the agent's toolset - these are the build scripts
data/              SQLite DB + agent workspace (gitignored)

The three scripts you run day to day stay at the root: setup.ps1, run.ps1,
run_tests.ps1. Everything under tools/ is only needed when cutting a release,
and each of those scripts sets its working directory to the repository root, so
they behave identically wherever you invoke them from.
```

## Status

Version 1.0.2, and single-maintainer by design. Windows-only in a deeper sense than
packaging: the shell tool spawns PowerShell, hardware detection reads WMI, the desktop
shell targets WebView2. Text-only — a vision model can be selected but never fed an
image. API keys are stored in plaintext in the local database and masked in every API
response, so the data folder is as sensitive as the keys in it. The file tools do
whole-file writes with no patch-style edit, so editing part of a large file means
rewriting all of it. Tool arguments aren't validated against their declared schema
before dispatch.

[Whitepaper §19](docs/WHITEPAPER.md) is the full accounting, including the structural
problems and what is planned.
