import { useEffect, useRef, useState } from 'react'
import {
  createDocument,
  deleteDocument,
  getDocument,
  listDocuments,
  streamEdit,
  updateDocument,
} from '../api'
import type { Document, DocumentSummary } from '../api'

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
  const [customOpen, setCustomOpen] = useState(false)
  const [custom, setCustom] = useState('')
  const [suggestion, setSuggestion] = useState<string | null>(null)
  const [streaming, setStreaming] = useState(false)
  const [error, setError] = useState('')
  // the span the current suggestion will replace, captured when the run starts
  const targetRef = useRef<{ start: number; end: number }>({ start: 0, end: 0 })
  const suggestionDoc = useRef<string | null>(null) // doc a pending suggestion belongs to
  const gotDeltaRef = useRef(false)
  const abortRef = useRef<AbortController | null>(null)
  const textRef = useRef<HTMLTextAreaElement>(null)
  const saveTimer = useRef<number | undefined>(undefined)
  // stale-proof mirror of the loaded doc so the debounced/flushed save always
  // targets the right document with its latest text, whatever React is rendering
  const docRef = useRef<{ id: string | null; title: string; content: string }>({
    id: null,
    title: '',
    content: '',
  })
  const dirtyRef = useRef(false)

  const refreshDocs = () => listDocuments().then(setDocs).catch(console.error)
  useEffect(() => {
    refreshDocs()
  }, [])

  async function saveNow() {
    if (!docRef.current.id || !dirtyRef.current) return
    dirtyRef.current = false
    try {
      await updateDocument(docRef.current.id, {
        title: docRef.current.title,
        content: docRef.current.content,
      })
      refreshDocs()
    } catch (e) {
      dirtyRef.current = true // leave it dirty so the next change retries
      setError(String(e))
    }
  }

  function flushSave() {
    window.clearTimeout(saveTimer.current)
    void saveNow() // reads docRef synchronously, then awaits - fire and forget
  }

  // flush any pending edit when leaving the editor (component unmount)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => () => flushSave(), [])

  // single writer for the loaded doc's title/content: mirrors to docRef and
  // schedules the debounced autosave
  function patchDoc(next: { title?: string; content?: string }) {
    if (next.title !== undefined) {
      setTitle(next.title)
      docRef.current.title = next.title
    }
    if (next.content !== undefined) {
      setContent(next.content)
      docRef.current.content = next.content
    }
    if (!docRef.current.id) return
    dirtyRef.current = true
    window.clearTimeout(saveTimer.current)
    saveTimer.current = window.setTimeout(() => void saveNow(), 700)
  }

  function abortStream() {
    abortRef.current?.abort()
    abortRef.current = null
    setStreaming(false)
    setSuggestion(null)
    suggestionDoc.current = null
  }

  function loadInto(doc: Document) {
    docRef.current = { id: doc.id, title: doc.title, content: doc.content }
    dirtyRef.current = false
    setActiveId(doc.id)
    setTitle(doc.title)
    setContent(doc.content)
    setError('')
  }

  async function openDoc(id: string) {
    if (id === activeId) return
    flushSave() // persist the current doc's pending edit before switching away
    abortStream() // drop any in-flight suggestion tied to the old doc
    loadInto(await getDocument(id))
  }

  async function newDoc() {
    flushSave()
    abortStream()
    const doc = await createDocument()
    await refreshDocs()
    loadInto(doc)
  }

  const canEdit = Boolean(providerId && model && activeId)

  // mode true/false = fixed; 'auto' = act on the selection if there is one
  async function runAction(instruction: string, mode: boolean | 'auto') {
    if (!canEdit || streaming) return
    const el = textRef.current
    const start = el ? el.selectionStart : 0 // read the LIVE selection, not lagged state
    const end = el ? el.selectionEnd : 0
    const needsSelection = mode === 'auto' ? end > start : mode
    const selection = content.slice(start, end)
    if (needsSelection && !selection.trim()) {
      setError('Select some text first for this action.')
      return
    }
    targetRef.current = needsSelection ? { start, end } : { start: end, end }
    suggestionDoc.current = activeId
    gotDeltaRef.current = false
    setError('')
    setSuggestion('')
    setStreaming(true)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      await streamEdit(
        { provider_id: providerId, model, instruction, selection, context: content },
        (ev) => {
          if (ev.type === 'text_delta' && ev.text) {
            gotDeltaRef.current = true
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
      // nothing produced (error, empty, or stopped before the first token):
      // don't leave an empty suggestion panel whose Accept would delete text
      if (!gotDeltaRef.current) {
        setSuggestion(null)
        suggestionDoc.current = null
      }
    }
  }

  function acceptSuggestion() {
    if (!suggestion || !suggestion.trim()) return // never accept an empty edit
    if (suggestionDoc.current !== activeId) return // belongs to a different doc
    const { start, end } = targetRef.current
    let replacement = stripFence(suggestion)
    // a revision (start !== end) replaces a span, so strip stray leading/
    // trailing whitespace the model added; an insertion keeps it as-is
    if (start !== end) replacement = replacement.trim()
    patchDoc({ content: content.slice(0, start) + replacement + content.slice(end) })
    setSuggestion(null)
    suggestionDoc.current = null
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
            role="button"
            tabIndex={0}
            aria-current={d.id === activeId}
            onClick={() => openDoc(d.id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                openDoc(d.id)
              }
            }}
          >
            <span className="doc-item-title" title={d.title}>
              {d.title || 'Untitled'}
            </span>
            <button
              className="icon-btn"
              title="Delete document"
              aria-label={`Delete document ${d.title || 'Untitled'}`}
              onClick={(e) => {
                e.stopPropagation()
                if (confirm(`Delete "${d.title}"?`)) {
                  if (d.id === activeId) {
                    abortStream()
                    docRef.current = { id: null, title: '', content: '' }
                    dirtyRef.current = false
                    setActiveId(null)
                    setContent('')
                    setTitle('')
                  }
                  deleteDocument(d.id).then(refreshDocs)
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
            onChange={(e) => patchDoc({ title: e.target.value })}
          />
          <div className="doc-actions">
            {ACTIONS.map((a) => (
              <button
                key={a.label}
                className="btn btn-compact"
                disabled={!canEdit || streaming || suggestion !== null}
                title={a.needsSelection ? 'Acts on the selected text' : 'Adds to the document'}
                onClick={() => runAction(a.instruction, a.needsSelection)}
              >
                {a.label}
              </button>
            ))}
            <button
              className="btn btn-compact"
              disabled={!canEdit || streaming || suggestion !== null}
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
                    runAction(custom.trim(), 'auto')
                  }
                }}
              />
              <button
                className="btn btn-primary"
                disabled={!canEdit || streaming || suggestion !== null || !custom.trim()}
                onClick={() => runAction(custom.trim(), 'auto')}
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
            // locked while a suggestion is pending so editing can't drift the
            // captured [start,end] the accepted replacement splices into
            disabled={streaming || suggestion !== null}
            onChange={(e) => patchDoc({ content: e.target.value })}
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
                    <button
                      className="btn btn-primary btn-compact"
                      disabled={!suggestion.trim()}
                      onClick={acceptSuggestion}
                    >
                      Accept
                    </button>
                    <button
                      className="btn btn-compact"
                      onClick={() => {
                        setSuggestion(null)
                        suggestionDoc.current = null
                      }}
                    >
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
