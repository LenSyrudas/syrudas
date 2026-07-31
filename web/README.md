# Syrudas AI — frontend

React + TypeScript + Vite. Built into `web/dist/`, which the Python server mounts
and serves; there is no separate frontend deployment.

Most of the time you do not need this directory directly — `.\setup.ps1` installs
and builds it, and `.\run_tests.ps1` runs its checks alongside the backend suites.

## Layout

| Path | What lives there |
|------|------------------|
| `src/App.tsx` | Shell: view switching, conversation list, provider/model reconciliation |
| `src/api.ts` | Every server call, plus the NDJSON stream reader |
| `src/chatItems.ts` | Pure reducer folding stream events into a thread, and the rebuild from stored messages |
| `src/components/` | One file per view (`ChatView`, `SettingsView`, `EditorView`, `ArenaView`, `CookbookView`) plus shared pieces |
| `src/theme.ts` | Appearance and colour-vision axes, applied as attributes on the document root |
| `src/test/` | Vitest setup |

`chatItems.ts` sits outside the components on purpose: it is stateful transform
logic whose bugs stay invisible until a specific event sequence occurs, so it is
kept pure and tested directly rather than through the UI.

## Working on it

```
npm run dev     # vite dev server, proxies /api to http://127.0.0.1:8040
npm run build   # tsc -b, then vite build into dist/
npm test        # vitest
npm run lint    # oxlint
```

`npm run dev` expects the Python server to already be running (`.\run.ps1` from
the repository root): the proxy forwards `/api`, and the dev server owns
everything else.
