import { useEffect, useRef, useState } from 'react'
import {
  getConversation,
  rewindConversation,
  streamChat,
  streamResearch,
  uploadAttachment,
} from '../api'
import type { Attachment, ChatRequest, GenParams } from '../api'
import {
  applyEvent,
  fileBlock,
  itemsFromMessages,
  parseUserContent,
  usageLabel,
} from '../chatItems'
import type { ChatItem } from '../chatItems'
import { copyToClipboard } from '../clipboard'
import type { Conversation, StreamEvent } from '../types'
import Markdown from './Markdown'
import ToolCallCard from './ToolCallCard'

export type { ChatItem, ResearchItem, ToolItem, Usage } from '../chatItems'

function CopyButton({ text, className = '' }: { text: string; className?: string }) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timerRef.current), [])
  return (
    <button
      className={`icon-btn copy-btn ${copied ? 'copied' : ''} ${className}`}
      title="Copy message text"
      aria-label={copied ? 'Copied' : 'Copy message text'}
      onClick={async () => {
        if (await copyToClipboard(text)) {
          setCopied(true)
          window.clearTimeout(timerRef.current)
          timerRef.current = window.setTimeout(() => setCopied(false), 1500)
        }
      }}
    >
      {copied ? '✓' : '⧉'}
    </button>
  )
}

interface Props {
  conversationId: string | null
  providerId: string
  model: string
  agentMode: boolean
  genParams: GenParams
  systemPrompt: string
  onConversationCreated: (id: string) => void
  // `initial` is true only for a genuine open (the mount load), false for
  // editLast/regenerate resyncs — lets the parent restore the model just once.
  onConversationLoaded: (conv: Conversation, initial: boolean) => void
  onStreamEnd: () => void
}

export default function ChatView({
  conversationId,
  providerId,
  model,
  agentMode,
  genParams,
  systemPrompt,
  onConversationCreated,
  onConversationLoaded,
  onStreamEnd,
}: Props) {
  const [items, setItems] = useState<ChatItem[]>([])
  // External launchers (e.g. the VS Code extension) prefill the composer via
  // ?prompt= - read once, then scrub it from the URL so reloads start clean.
  const [input, setInput] = useState(() => {
    const prompt = new URLSearchParams(window.location.search).get('prompt')
    if (prompt) window.history.replaceState(null, '', window.location.pathname)
    return prompt ?? ''
  })
  const [streaming, setStreaming] = useState(false)
  // busy covers rewind round-trips before streaming starts; busyRef is the
  // SYNCHRONOUS re-entrancy guard (React state lags awaits, so a double-click
  // would otherwise rewind twice and destroy an extra user turn)
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const [pending, setPending] = useState<Attachment[]>([])
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  // a research run's report has no rewind story (regenerate would delete it and
  // reply with a plain, un-cited answer) - suppress edit/regenerate for it
  const [isResearch, setIsResearch] = useState(false)
  // null until this instance owns a conversation: set by the load effect for
  // existing chats, or by the meta event when a send creates one mid-stream.
  // Must NOT be seeded from the prop, or the load effect's "already live"
  // guard skips loading when an existing conversation is opened.
  const convIdRef = useRef<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  async function addFiles(list: FileList | File[]) {
    setUploading(true)
    for (const file of Array.from(list)) {
      try {
        const att = await uploadAttachment(file)
        setPending((prev) => [...prev, att])
      } catch (e) {
        setItems((prev) => [...prev, { kind: 'error', content: `Could not attach ${file.name}: ${e}` }])
      }
    }
    setUploading(false)
  }

  async function loadConversation(id: string, initial = false) {
    const conv = await getConversation(id)
    setItems(itemsFromMessages(conv))
    onConversationLoaded(conv, initial)
    return conv
  }

  function markResearchDone() {
    setItems((prev) =>
      prev.map((it) => (it.kind === 'research' && !it.done ? { ...it, done: true } : it)),
    )
  }

  useEffect(() => {
    if (!conversationId) return
    // If this component created the conversation mid-stream, items are already live
    if (convIdRef.current === conversationId) return
    convIdRef.current = conversationId
    loadConversation(conversationId, true).catch(console.error)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [items])

  function handleEvent(ev: StreamEvent) {
    if (ev.type === 'meta' && ev.conversation_id) {
      if (!convIdRef.current) {
        convIdRef.current = ev.conversation_id
        onConversationCreated(ev.conversation_id)
      }
      return
    }
    setItems((prev) => applyEvent(prev, ev))
  }

  function cleanParams(): GenParams | undefined {
    const params: GenParams = {}
    if (genParams.temperature !== undefined) params.temperature = genParams.temperature
    if (genParams.max_tokens !== undefined) params.max_tokens = genParams.max_tokens
    return Object.keys(params).length ? params : undefined
  }

  async function runStream(req: ChatRequest) {
    setStreaming(true)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      await streamChat(req, handleEvent, controller.signal)
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        setItems((prev) => [...prev, { kind: 'error', content: String(e) }])
      }
    } finally {
      setStreaming(false)
      abortRef.current = null
      onStreamEnd()
    }
  }

  async function send() {
    const typed = input.trim()
    if ((!typed && pending.length === 0) || streaming || busyRef.current || uploading || !providerId || !model) return
    const message = [typed, ...pending.map(fileBlock)].filter(Boolean).join('\n\n')
    setInput('')
    setPending([])
    setIsResearch(false) // a normal turn makes this an ordinary conversation
    setItems((prev) => [...prev, { kind: 'user', content: message }])
    await runStream({
      conversation_id: convIdRef.current ?? undefined,
      provider_id: providerId,
      model,
      message,
      agent_mode: agentMode,
      // creation only: existing conversations are edited via PATCH, so a
      // stale tab can't clobber a prompt changed elsewhere
      system_prompt: convIdRef.current ? undefined : systemPrompt || undefined,
      params: cleanParams(),
    })
  }

  async function research() {
    const typed = input.trim()
    if (!typed || streaming || busyRef.current || uploading || !providerId || !model) return
    if (pending.length) return // research is web-only; attachments aren't read
    setInput('')
    setIsResearch(true)
    setItems((prev) => [...prev, { kind: 'user', content: typed }])
    setStreaming(true)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      await streamResearch(
        { provider_id: providerId, model, question: typed, params: cleanParams() },
        handleEvent,
        controller.signal,
      )
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        setItems((prev) => [...prev, { kind: 'error', content: String(e) }])
      }
    } finally {
      setStreaming(false)
      abortRef.current = null
      markResearchDone() // e.g. on abort, no 'done' event arrives
      onStreamEnd()
    }
  }

  async function regenerate() {
    const convId = convIdRef.current
    if (busyRef.current || !convId || streaming || !providerId || !model) return
    busyRef.current = true
    setBusy(true)
    // optimistic: drop everything after the last user message; the server
    // does the authoritative rewind inside the same request and restores it
    // if the provider fails before producing anything
    setItems((prev) => {
      const lastUser = prev.map((it) => it.kind).lastIndexOf('user')
      return lastUser >= 0 ? prev.slice(0, lastUser + 1) : prev
    })
    try {
      await runStream({
        conversation_id: convId,
        provider_id: providerId,
        model,
        agent_mode: agentMode,
        regenerate: true,
        params: cleanParams(),
      })
      await loadConversation(convId) // resync (picks up server-side rollback too)
    } catch (e) {
      setItems((prev) => [...prev, { kind: 'error', content: String(e) }])
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  async function editLast() {
    const convId = convIdRef.current
    if (busyRef.current || !convId || streaming || !canSend) return
    busyRef.current = true
    setBusy(true)
    try {
      const res = await rewindConversation(convId, true)
      await loadConversation(convId)
      const { text, files } = parseUserContent(res.removed_user_content ?? '')
      setInput(text)
      setPending(files.map((f) => ({
        name: f.name, content: f.content, chars: f.content.length, truncated: false,
      })))
      textareaRef.current?.focus()
    } catch (e) {
      setItems((prev) => [...prev, { kind: 'error', content: String(e) }])
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  function markToolResolved(approvalId: string, approved: boolean) {
    setItems((prev) =>
      prev.map((it) =>
        it.kind === 'tool' && it.approvalId === approvalId
          ? { ...it, status: approved ? 'running' : 'denied', approvalId: undefined }
          : it,
      ),
    )
  }

  const canSend = Boolean(providerId && model)
  const lastUserIndex = items.map((it) => it.kind).lastIndexOf('user')
  const canRewind =
    Boolean(convIdRef.current) && !streaming && !busy && canSend && lastUserIndex >= 0 &&
    !isResearch

  return (
    <div className="chat">
      <div className="thread" ref={scrollRef}>
        {items.length === 0 && (
          <div className="thread-empty">
            <div className="thread-empty-logo">👁</div>
            <h2>Syrudas AI</h2>
            <p>
              {canSend
                ? agentMode
                  ? 'Agent mode: the model can plan and use tools. Shell commands, web fetches and writes outside the workspace wait for your approval.'
                  : 'Ask anything. Swap models any time from the picker above.'
                : 'Add a model provider in Settings to get started.'}
            </p>
          </div>
        )}
        {items.map((item, i) => {
          switch (item.kind) {
            case 'user': {
              const { text, files } = parseUserContent(item.content)
              return (
                <div key={i} className="msg user">
                  <div className="bubble">
                    {files.length > 0 && (
                      <div className="bubble-files">
                        {files.map((f, fi) => (
                          <details key={fi} className="file-chip">
                            <summary>📎 {f.name}</summary>
                            <pre>{f.content.slice(0, 5000)}{f.content.length > 5000 ? '\n…' : ''}</pre>
                          </details>
                        ))}
                      </div>
                    )}
                    {text}
                  </div>
                  <CopyButton text={text || item.content} className="msg-action" />
                  {canRewind && i === lastUserIndex && (
                    <button
                      className="icon-btn msg-action"
                      title="Edit this message (removes the replies after it)"
                      aria-label="Edit this message (removes the replies after it)"
                      onClick={editLast}
                    >
                      ✎
                    </button>
                  )}
                </div>
              )
            }
            case 'assistant':
              return (
                <div key={i} className="msg assistant">
                  <Markdown>{item.content}</Markdown>
                  {item.streaming && <span className="cursor">▌</span>}
                  {!item.streaming && (
                    <div className="msg-toolbar">
                      <CopyButton text={item.content} />
                      {item.usage && usageLabel(item.usage) && (
                        <span className="msg-usage" title="Prompt / completion tokens">
                          {usageLabel(item.usage)}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              )
            case 'tool':
              return (
                <ToolCallCard
                  key={i}
                  item={item}
                  onResolved={(approved) =>
                    item.approvalId && markToolResolved(item.approvalId, approved)
                  }
                />
              )
            case 'research':
              return (
                <details key={i} className="research-card" open={!item.done}>
                  <summary>
                    {item.done ? '🔎 Research complete' : `🔎 Researching — ${item.phase}…`}
                  </summary>
                  <ol className="research-steps">
                    {item.steps.map((s, si) => (
                      <li key={si}>{s}</li>
                    ))}
                  </ol>
                </details>
              )
            case 'error':
              return (
                <div key={i} className="msg error-msg">
                  ⚠ {item.content}
                </div>
              )
          }
        })}
        {streaming && items[items.length - 1]?.kind === 'user' && (
          <div className="msg assistant thinking">…</div>
        )}
        {canRewind && canSend && (
          <div className="thread-actions">
            <button className="btn btn-compact" title="Delete the last reply and generate a new one" onClick={regenerate}>
              ↻ Regenerate
            </button>
          </div>
        )}
      </div>
      <div
        className={`composer-area ${dragOver ? 'drag-over' : ''}`}
        onDragOver={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragOver(false)
          if (canSend && e.dataTransfer.files.length) addFiles(e.dataTransfer.files)
        }}
      >
        {(pending.length > 0 || uploading) && (
          <div className="pending-files">
            {pending.map((a, i) => (
              <span key={i} className="pending-chip" title={`${a.chars.toLocaleString()} chars`}>
                📎 {a.name}
                {a.truncated ? ' (truncated)' : ''}
                <button
                  className="icon-btn"
                  title="Remove attachment"
                  aria-label={`Remove attachment ${a.name}`}
                  onClick={() => setPending((prev) => prev.filter((_, pi) => pi !== i))}
                >
                  ✕
                </button>
              </span>
            ))}
            {uploading && <span className="pending-chip">⏳ reading…</span>}
          </div>
        )}
        <div className="composer">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            style={{ display: 'none' }}
            onChange={(e) => {
              if (e.target.files?.length) addFiles(e.target.files)
              e.target.value = ''
            }}
          />
          <button
            className="btn attach-btn"
            title="Attach files (text, code, CSV, JSON, PDF) - or drag & drop"
            aria-label="Attach files"
            disabled={!canSend || streaming}
            onClick={() => fileInputRef.current?.click()}
          >
            📎
          </button>
          <textarea
            ref={textareaRef}
            value={input}
            placeholder={canSend ? 'Message Syrudas…  (Enter to send, Shift+Enter for newline)' : 'Configure a provider in Settings first'}
            disabled={!canSend}
            rows={Math.min(8, Math.max(1, input.split('\n').length))}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                send()
              }
            }}
          />
          {streaming ? (
            <button className="btn btn-danger" onClick={() => abortRef.current?.abort()}>
              Stop
            </button>
          ) : (
            <>
              {!convIdRef.current && (
                <button
                  className="btn"
                  title={
                    pending.length
                      ? "Deep Research is web-only and doesn't read attachments - remove them to research"
                      : 'Deep Research: search the web, read sources, and write a cited report'
                  }
                  disabled={!canSend || uploading || !input.trim() || pending.length > 0}
                  onClick={research}
                >
                  🔎 Research
                </button>
              )}
              <button
                className="btn btn-primary"
                disabled={!canSend || uploading || (!input.trim() && pending.length === 0)}
                onClick={send}
              >
                Send
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
