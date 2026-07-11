// Syrudas AI - VS Code extension.
// Embeds the local Syrudas workspace in a webview panel and prefills it with
// editor selections via the UI's ?prompt= parameter.
const vscode = require('vscode')

let panel = null

function baseUrl() {
  const url = vscode.workspace.getConfiguration('syrudas').get('url') || 'http://127.0.0.1:8040'
  return String(url).replace(/\/+$/, '')
}

async function serverReachable() {
  try {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 2500)
    const resp = await fetch(baseUrl() + '/api/health', { signal: controller.signal })
    clearTimeout(timer)
    return resp.ok
  } catch {
    return false
  }
}

function panelHtml(src) {
  return `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    html, body { margin: 0; padding: 0; height: 100%; overflow: hidden; }
    iframe { border: 0; width: 100%; height: 100vh; }
  </style>
</head>
<body>
  <iframe src="${src}" allow="clipboard-read; clipboard-write"></iframe>
</body>
</html>`
}

async function showPanel(prompt) {
  if (!(await serverReachable())) {
    const pick = await vscode.window.showWarningMessage(
      'Syrudas AI is not running at ' + baseUrl() + '. Start SyrudasAI.exe (or run.ps1) and retry.',
      'Retry',
    )
    if (pick === 'Retry') return showPanel(prompt)
    return
  }

  let target = baseUrl() + '/'
  if (prompt) target += '?prompt=' + encodeURIComponent(prompt)
  const external = (await vscode.env.asExternalUri(vscode.Uri.parse(target))).toString()

  if (!panel) {
    panel = vscode.window.createWebviewPanel(
      'syrudasAI', 'Syrudas AI', vscode.ViewColumn.Beside,
      { enableScripts: true, retainContextWhenHidden: true },
    )
    panel.onDidDispose(() => { panel = null })
  } else {
    panel.reveal(undefined, true)
  }
  panel.webview.html = panelHtml(external)
}

const MAX_SELECTION_CHARS = 6000

function selectionPrompt(editor) {
  let code = editor.document.getText(editor.selection)
  if (code.length > MAX_SELECTION_CHARS) {
    code = code.slice(0, MAX_SELECTION_CHARS) + '\n... [selection truncated]'
  }
  const lang = editor.document.languageId || ''
  const file = vscode.workspace.asRelativePath(editor.document.uri)
  const line = editor.selection.start.line + 1
  return 'About this code from ' + file + ':' + line + ':\n' +
    '```' + lang + '\n' + code + '\n```\n\n'
}

function activate(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand('syrudas.open', () => showPanel()),
    vscode.commands.registerCommand('syrudas.askSelection', () => {
      const editor = vscode.window.activeTextEditor
      if (!editor || editor.selection.isEmpty) {
        vscode.window.showInformationMessage('Select some code first, then ask Syrudas about it.')
        return
      }
      return showPanel(selectionPrompt(editor))
    }),
  )
}

function deactivate() {}

module.exports = { activate, deactivate }
