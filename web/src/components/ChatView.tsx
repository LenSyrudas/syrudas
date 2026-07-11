import { useEffect, useRef, useState } from 'react'
import { getConversation, rewindConversation, streamChat, uploadAttachment } from '../api'
import type { Attachment, ChatRequest, GenParams } from '../api'
import type { Conversation, StreamEvent, ToolCall } from '../types'
import Markdown from './Markdown'
import ToolCallCard from './ToolCallCard'

// consumes the \n on BOTH sides that fileBlock() adds, or every edit/resend
// cycle would grow the attachment by one newline
const FILE_BLOCK = /<file name="([^"]*)">\n?([\s\S]*?)\n?<\/file>/g

/** Split a stored user message into typed text and attached-file blocks. */
function parseUserContent(content: string): { text: string; files: { name: string; content: string }[] } {
  const files: { name: string; content: string }[] = []
  const text = content
    .replace(FILE_BLOCK, (_m, name: string, body: string) => {
      files.push({ name, content: body })
      return ''
    })
    .trim()
  return { text, files }
}

function fileBlock(a: Attachment): string {
  return `<file name="${a.name.replace(/"/g, "'")}">\n${a.content}\n</file>`
}

export interface ToolItem {
  kind: 'tool'
  call: ToolCall
  result?: string
  status: 'running' | 'done' | 'awaiting_approval' | 'denied' | 'error'
  approvalId?: string
}

export type ChatItem =
  | { kind: 'user'; content: string }
  | { kind: 'assistant'; content: string; streaming?: boolean }
  | ToolItem
  | { kind: 'error'; content: string }

interface Props {
  conversationId: string | null
  providerId: string
  model: string
  agentMode: boolean
  genParams: GenParams
  systemPrompt: string
  onConversationCreated: (id: string) => void
  onConversationLoaded: (conv: Conversation) => void
  onStreamEnd: () => void
}

function itemsFromMessages(conv: Conversation): ChatItem[] {
  const loaded: ChatItem[] = []
  for (const m of conv.messages ?? []) {
    if (m.role === 'user') {
      loaded.push({ kind: 'user', content: m.content })
    } else if (m.role === 'assistant') {
      if (m.content) loaded.push({ kind: 'assistant', content: m.content })
      for (const tc of m.tool_calls ?? []) {
        loaded.push({ kind: 'tool', call: tc, status: 'done' })
      }
    } else if (m.role === 'tool') {
      const tool = loaded.find(
        (it): it is ToolItem =>
          it.kind === 'tool' && it.call.id === m.tool_call_id && it.result === undefined,
      )
      if (tool) tool.result = m.content
    }
  }
  return loaded
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

  async function loadConversation(id: string) {
    const conv = await getConversation(id)
    setItems(itemsFromMessages(conv))
    onConversationLoaded(conv)
    return conv
  }

  useEffect(() => {
    if (!conversationId) return
    // If this component created the conversation mid-stream, items are already live
    if (convIdRef.current === conversationId) return
    convIdRef.current = conversationId
    loadConversation(conversationId).catch(console.error)
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
    setItems((prev) => {
      const next = [...prev]
      const last = next[next.length - 1]
      switch (ev.type) {
        case 'text_delta': {
          if (last?.kind === 'assistant' && last.streaming) {
            next[next.length - 1] = { ...last, content: last.content + (ev.text ?? '') }
          } else {
            next.push({ kind: 'assistant', content: ev.text ?? '', streaming: true })
          }
          break
        }
        case 'tool_call': {
          if (ev.tool_call) next.push({ kind: 'tool', call: ev.tool_call, status: 'running' })
          break
        }
        case 'approval_required': {
          const idx = next.findIndex(
            (it) => it.kind === 'tool' && it.call.id === ev.tool_call?.id,
          )
          if (idx >= 0) {
            next[idx] = {
              ...(next[idx] as ToolItem),
              status: 'awaiting_approval',
              approvalId: ev.approval_id,
            }
          } else if (ev.tool_call) {
            next.push({
              kind: 'tool',
              call: ev.tool_call,
              status: 'awaiting_approval',
              approvalId: ev.approval_id,
            })
          }
          break
        }
        case 'tool_result': {
          const idx = next.findIndex(
            (it) => it.kind === 'tool' && it.call.id === ev.tool_call_id && it.result === undefined,
          )
          if (idx >= 0) {
            const tool = next[idx] as ToolItem
            next[idx] = {
              ...tool,
              result: ev.content ?? '',
              status: tool.status === 'denied' ? 'denied' : 'done',
            }
          }
          break
        }
        case 'error': {
          next.push({ kind: 'error', content: ev.message ?? 'Unknown error' })
          break
        }
        case 'done': {
          for (let i = 0; i < next.length; i++) {
            const it = next[i]
            if (it.kind === 'assistant' && it.streaming) next[i] = { ...it, streaming: false }
          }
          break
        }
      }
      return next
    })
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
    Boolean(convIdRef.current) && !streaming && !busy && canSend && lastUserIndex >= 0

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
                  ? 'Agent mode: the model can plan and use tools. Shell commands wait for your approval.'
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
                  {canRewind && i === lastUserIndex && (
                    <button
                      className="icon-btn msg-action"
                      title="Edit this message (removes the replies after it)"
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
              if (e.key === 'Enter' && !e.shiftKey) {
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
            <button
              className="btn btn-primary"
              disabled={!canSend || uploading || (!input.trim() && pending.length === 0)}
              onClick={send}
            >
              Send
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
