Syrudas AI
==========

A self-hosted AI workspace that runs entirely on your own PC.
Chat with local or API language models, let them use tools in agent
mode, and connect MCP servers - private, local-first, no telemetry.

Quick start
-----------
1. Unzip this download. Inside is a "SyrudasAI" folder - move it
   wherever you'd like to keep it (e.g. C:\SyrudasAI). Everything the
   app needs lives in that folder, and it stores its data there too, so
   keep the folder together.
2. Install a model backend if you don't have one. The easiest is
   Ollama (https://ollama.com), then in a terminal run for example:
       ollama pull llama3.1:8b
3. Double-click SyrudasAI.exe. A window opens; if Ollama or LM Studio
   is running, Syrudas finds it automatically and you can start
   chatting right away.

Notes
-----
- Windows says "Windows protected your PC"? That is SmartScreen being
  cautious about unsigned apps. Click "More info", then "Run anyway".
- Requirements: Windows 10/11 with the WebView2 runtime (built into
  Windows 11; if the window doesn't open, Syrudas falls back to your
  default browser).
- Attach files to any message with the paperclip button or by dragging
  them onto the chat: code, text, CSV, JSON, logs, and PDFs.
- Agent mode needs a model that supports tool calling, e.g.
  llama3.1:8b. Shell commands always ask for your approval first.
  By default its file tools only see the workspace folder; grant more
  folders under Settings -> Agent file access.
- Your conversations, settings and API keys live in the "data" folder
  next to the exe and never leave your machine. Logs: data\syrudas.log
- Add more model backends under Settings -> Model providers. Anything
  OpenAI-compatible works: Ollama, LM Studio, OpenRouter, OpenAI, vLLM.
  Claude (Anthropic) and Gemini (Google) connectors are included too -
  pick their provider type and paste your API key to use them.
- Python provider plugins can be dropped into the "plugins" folder.

License: MIT (see LICENSE.txt)
