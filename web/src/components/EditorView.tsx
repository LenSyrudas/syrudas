import { useEffect, useRef, useState } from 'react'
import {
  createDocument,
  deleteDocument,
  getDocument,
  listDocuments,
  streamEdit,
  updateDocument,
} from '../api'
import type { DocumentSummary } from '../api'

interface Props {
  providerId: string
  model: string
}

const ACTIONS: { label: string; instruction: string; needsSelection: boolean }[] = [
  { label: 'Improve', instruction: 'Improve the clarity, flow and word choice without changing the meaning.', needsSelection: true },
  { label: 'Shorten', instruction: 'Make this more concise while keeping the key points.', needsSelection: true },
  { label: 'Expand', instruction: 'Expand this with more detail and supporting explanation.', needsSelection: true },
  { label: 'Fix grammar', instruction: 'Fix spelling, grammar and punctuation. Change nothing else.', needsSelection: true },
  { label: 'Continue', instruction: 'Continue writing from where the document leaves off, in the same voice.', needsSelection: false },
]

// strip a wrapping ``` code fence a model sometimes adds around its output
function stripFence(text: string): string {
  const m = text.match(/^\s*```[a-zA-Z]*\n([\s\S]*?)\n```\s*$/)
  return m ? m[1] : text
}

export default function EditorView({ providerId, model }: Props) {
  const [docs, setDocs] = useState<DocumentSummary[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [sel, setSel] = useState<{ start: number; end: number }>({ start: 0, end: 0 })
  const [customOpen, setCustomOpen] = useState(false)
  const [custom, setCustom] = useState('')
  const [suggestion, setSuggestion] = useState<string | null>(null)
  const [streaming, setStreaming] = useState(false)
  const [error, setError] = useState('')
  // the span the current suggestion will replace, captured when the run starts
  const targetRef = useRef<{ start: number; end: number }>({ start: 0, end: 0 })
  const abortRef = useRef<AbortController | null>(null)
  const textRef = useRef<HTMLTextAreaElement>(null)
  const saveTimer = useRef<number | undefined>(undefined)
  const loadedId = useRef<string | null>(null)

  const refreshDocs = () => listDocuments().then(setDocs).catch(console.error)
  useEffect(() => {
    refreshDocs()
  }, [])

  useEffect(() => () => window.clearTimeout(saveTimer.current), [])

  async function openDoc(id: string) {
    const doc = await getDocument(id)
    loadedId.current = id
    setActiveId(id)
    setTitle(doc.title)
    setContent(doc.content)
    setSuggestion(null)
    setError('')
  }

  async function newDoc() {
    const doc = await createDocument()
    await refreshDocs()
    loadedId.current = doc.id
    setActiveId(doc.id)
    setTitle(doc.title)
    setContent('')
    setSuggestion(null)
  }

  // debounced autosave whenever the loaded doc's title/content changes
  useEffect(() => {
    if (!activeId || loadedId.current !== activeId) return
    window.clearTimeout(saveTimer.current)
    saveTimer.current = window.setTimeout(() => {
      updateDocument(activeId, { title, content })
        .then(() => refreshDocs())
        .catch((e) => setError(String(e)))
    }, 700)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [title, content])

  function captureSel() {
    const el = textRef.current
    if (el) setSel({ start: el.selectionStart, end: el.selectionEnd })
  }

  const canEdit = Boolean(providerId && model && activeId)

  async function runAction(instruction: string, needsSelection: boolean) {
    if (!canEdit || streaming) return
    const start = sel.start
    const end = sel.end
    const selection = content.slice(start, end)
    if (needsSelection && !selection.trim()) {
      setError('Select some text first for this action.')
      return
    }
    // no selection = insert at the cursor (end of selection)
    targetRef.current = needsSelection ? { start, end } : { start: end, end }
    setError('')
    setSuggestion('')
    setStreaming(true)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      await streamEdit(
        {
          provider_id: providerId,
          model,
          instruction,
          selection,
          context: content,
        },
        (ev) => {
          if (ev.type === 'text_delta' && ev.text) {
            const delta = ev.text
            setSuggestion((s) => (s ?? '') + delta)
          } else if (ev.type === 'error') {
            setError(ev.message ?? 'edit failed')
          }
        },
        controller.signal,
      )
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setError(String(e))
    } finally {
      setStreaming(false)
      abortRef.current = null
    }
  }

  function acceptSuggestion() {
    if (suggestion === null) return
    const { start, end } = targetRef.current
    let replacement = stripFence(suggestion)
    // a revision (start !== end) replaces a span, so strip stray leading/
    // trailing whitespace the model added; an insertion keeps it as-is
    if (start !== end) replacement = replacement.trim()
    setContent((c) => c.slice(0, start) + replacement + c.slice(end))
    setSuggestion(null)
  }

  return (
    <div className="editor">
      <aside className="doc-list">
        <button className="btn btn-primary" onClick={newDoc}>
          + New document
        </button>
        {docs.map((d) => (
          <div
            key={d.id}
            className={`doc-item ${d.id === activeId ? 'active' : ''}`}
            onClick={() => openDoc(d.id)}
          >
            <span className="doc-item-title" title={d.title}>
              {d.title || 'Untitled'}
            </span>
            <button
              className="icon-btn"
              title="Delete document"
              onClick={(e) => {
                e.stopPropagation()
                if (confirm(`Delete "${d.title}"?`)) {
                  deleteDocument(d.id).then(() => {
                    if (d.id === activeId) {
                      setActiveId(null)
                      setContent('')
                      setTitle('')
                    }
                    refreshDocs()
                  })
                }
              }}
            >
              ✕
            </button>
          </div>
        ))}
        {docs.length === 0 && <div className="conv-empty">No documents yet</div>}
      </aside>

      {activeId ? (
        <div className="doc-pane">
          <input
            className="doc-title"
            value={title}
            placeholder="Untitled"
            onChange={(e) => setTitle(e.target.value)}
          />
          <div className="doc-actions">
            {ACTIONS.map((a) => (
              <button
                key={a.label}
                className="btn btn-compact"
                disabled={!canEdit || streaming}
                title={a.needsSelection ? 'Acts on the selected text' : 'Adds to the document'}
                onClick={() => runAction(a.instruction, a.needsSelection)}
              >
                {a.label}
              </button>
            ))}
            <button
              className="btn btn-compact"
              disabled={!canEdit || streaming}
              onClick={() => setCustomOpen((v) => !v)}
            >
              ✏ Custom
            </button>
            {!canEdit && <span className="muted">Pick a model in the top bar to enable AI edits</span>}
          </div>
          {customOpen && (
            <div className="doc-custom">
              <input
                value={custom}
                placeholder="Describe the edit (acts on selection, or inserts if nothing is selected)…"
                onChange={(e) => setCustom(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.nativeEvent.isComposing && custom.trim()) {
                    runAction(custom.trim(), Boolean(content.slice(sel.start, sel.end).trim()))
                  }
                }}
              />
              <button
                className="btn btn-primary"
                disabled={!canEdit || streaming || !custom.trim()}
                onClick={() =>
                  runAction(custom.trim(), Boolean(content.slice(sel.start, sel.end).trim()))
                }
              >
                Run
              </button>
            </div>
          )}
          {error && <div className="form-error">⚠ {error}</div>}
          <textarea
            ref={textRef}
            className="doc-body"
            value={content}
            placeholder="Start writing… select text and use the buttons above for AI edits."
            onChange={(e) => setContent(e.target.value)}
            onSelect={captureSel}
            onKeyUp={captureSel}
            onMouseUp={captureSel}
          />
          {suggestion !== null && (
            <div className="doc-suggestion">
              <div className="doc-suggestion-head">
                <strong>Suggestion</strong>
                {streaming ? (
                  <button className="btn btn-danger btn-compact" onClick={() => abortRef.current?.abort()}>
                    Stop
                  </button>
                ) : (
                  <span className="row">
                    <button className="btn btn-primary btn-compact" onClick={acceptSuggestion}>
                      Accept
                    </button>
                    <button className="btn btn-compact" onClick={() => setSuggestion(null)}>
                      Reject
                    </button>
                  </span>
                )}
              </div>
              <pre className="doc-suggestion-body">{suggestion || (streaming ? '…' : '(empty)')}</pre>
            </div>
          )}
        </div>
      ) : (
        <div className="doc-pane doc-empty">
          <p className="muted">Select a document or create a new one to start writing.</p>
        </div>
      )}
    </div>
  )
}
