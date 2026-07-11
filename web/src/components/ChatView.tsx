import { useEffect, useRef, useState } from 'react'
import { getConversation, streamChat } from '../api'
import type { StreamEvent, ToolCall } from '../types'
import Markdown from './Markdown'
import ToolCallCard from './ToolCallCard'

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
  onConversationCreated: (id: string) => void
  onStreamEnd: () => void
}

export default function ChatView({
  conversationId,
  providerId,
  model,
  agentMode,
  onConversationCreated,
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
  const convIdRef = useRef<string | null>(conversationId)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (!conversationId) return
    // If this component created the conversation mid-stream, items are already live
    if (convIdRef.current === conversationId) return
    convIdRef.current = conversationId
    getConversation(conversationId)
      .then((conv) => {
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
        setItems(loaded)
      })
      .catch(console.error)
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

  async function send() {
    const message = input.trim()
    if (!message || streaming || !providerId || !model) return
    setInput('')
    setItems((prev) => [...prev, { kind: 'user', content: message }])
    setStreaming(true)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      await streamChat(
        {
          conversation_id: convIdRef.current ?? undefined,
          provider_id: providerId,
          model,
          message,
          agent_mode: agentMode,
        },
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
      onStreamEnd()
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
            case 'user':
              return (
                <div key={i} className="msg user">
                  <div className="bubble">{item.content}</div>
                </div>
              )
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
      </div>
      <div className="composer">
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
          <button className="btn btn-primary" disabled={!canSend || !input.trim()} onClick={send}>
            Send
          </button>
        )}
      </div>
    </div>
  )
}
