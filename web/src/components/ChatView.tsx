import { useEffect, useRef, useState } from 'react'
import {
  detectProviders,
  getAutoTools,
  getConversation,
  pauseRun,
  resumeRun,
  rewindConversation,
  setAutoTools,
  streamChat,
  streamResearch,
  uploadAttachment,
  uploadMedia,
} from '../api'
import type { Attachment, ChatRequest, GenParams } from '../api'
import {
  applyEvent,
  fileBlock,
  groupToolRuns,
  itemsFromMessages,
  parseUserContent,
  usageLabel,
} from '../chatItems'
import type { ChatItem, ToolItem } from '../chatItems'
import { copyToClipboard } from '../clipboard'
import { mediaUrl } from '../types'
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

/** Consecutive tool calls as one collapsible block.
 *
 *  It forces itself open while anything inside is running or awaiting
 *  approval - an approval prompt hidden in a collapsed group would stall the
 *  run with no visible reason - and folds back once the run settles, unless
 *  the user opened it by hand.
 */
function ToolGroup({
  items,
  onResolved,
  onAlwaysAllow,
}: {
  items: ToolItem[]
  onResolved: (item: ToolItem, approved: boolean) => void
  onAlwaysAllow?: (toolName: string) => void
}) {
  const [open, setOpen] = useState(false)
  const active = items.some(
    (it) => it.status === 'running' || it.status === 'awaiting_approval',
  )
  const shown = open || active
  const failed = items.filter((it) => it.status === 'error' || it.status === 'denied').length
  const current = [...items]
    .reverse()
    .find((it) => it.status === 'running' || it.status === 'awaiting_approval')
  const summary = current
    ? current.status === 'awaiting_approval'
      ? `⏸ approval: ${current.call.name}`
      : `⏳ ${current.call.name}`
    : failed > 0
      ? `✗ ${failed} failed`
      : '✓ done'
  const names = [...new Set(items.map((it) => it.call.name))].join(', ')
  return (
    <div className={`tool-group ${active ? 'active' : ''}`}>
      <div className="tool-group-head" onClick={() => setOpen(!open)} title={names}>
        <span>
          {shown ? '▾' : '▸'} 🔧 {items.length} tool steps
        </span>
        <span className="tool-group-summary">{summary}</span>
      </div>
      {shown &&
        items.map((it, i) => (
          <ToolCallCard
            key={i}
            item={it}
            onResolved={(approved) => onResolved(it, approved)}
            onAlwaysAllow={onAlwaysAllow}
          />
        ))}
    </div>
  )
}

export interface Draft {
  input: string
  pending: Attachment[]
}

interface Props {
  draft: Draft
  onDraftChange: (d: Draft) => void
  conversationId: string | null
  providerId: string
  model: string
  agentMode: boolean
  /** auto mode: gated tools run without the per-call approval prompt */
  autoMode: boolean
  genParams: GenParams
  systemPrompt: string
  onConversationCreated: (id: string) => void
  // `initial` is true only for a genuine open (the mount load), false for
  // editLast/regenerate resyncs — lets the parent restore the model just once.
  onConversationLoaded: (conv: Conversation, initial: boolean) => void
  onStreamEnd: () => void
  /** a backend was just detected — reload the provider list */
  onProvidersChanged: () => void
  onOpenSettings: () => void
}

export default function ChatView({
  draft,
  onDraftChange,
  conversationId,
  providerId,
  model,
  agentMode,
  autoMode,
  genParams,
  systemPrompt,
  onConversationCreated,
  onConversationLoaded,
  onStreamEnd,
  onProvidersChanged,
  onOpenSettings,
}: Props) {
  const [items, setItems] = useState<ChatItem[]>([])
  // Owned by App and keyed by conversation, so switching chats (which remounts
  // this component) no longer discards a typed prompt or an uploaded file.
  const input = draft.input
  const pending = draft.pending
  const setInput = (v: string | ((p: string) => string)) =>
    onDraftChange({ ...draftRef.current, input: typeof v === 'function' ? v(draftRef.current.input) : v })
  const setPending = (v: Attachment[] | ((p: Attachment[]) => Attachment[])) =>
    onDraftChange({ ...draftRef.current, pending: typeof v === 'function' ? v(draftRef.current.pending) : v })
  // a ref so the setters above see the latest draft even when several fire
  // before React re-renders (upload completes while the user keeps typing)
  const draftRef = useRef(draft)
  draftRef.current = draft
  // Any external caller can prefill the composer via ?prompt= (a shortcut, a
  // script, another editor) - read once, then scrub it from the URL.
  const promptRead = useRef(false)
  if (!promptRead.current) {
    promptRead.current = true
    const prompt = new URLSearchParams(window.location.search).get('prompt')
    if (prompt) {
      window.history.replaceState(null, '', window.location.pathname)
      if (!draft.input) onDraftChange({ ...draft, input: prompt })
    }
  }
  const [streaming, setStreaming] = useState(false)
  // queued follow-ups share the streaming flag with the active run, so a
  // count, not a boolean: the last finisher turns it off
  const activeStreams = useRef(0)
  const streamStarted = () => {
    activeStreams.current += 1
    setStreaming(true)
  }
  const streamEnded = () => {
    activeStreams.current = Math.max(0, activeStreams.current - 1)
    if (activeStreams.current === 0) setStreaming(false)
  }
  // follow-ups sent while a run is active, waiting their turn server-side
  const [queued, setQueued] = useState<
    { id: number; text: string; controller: AbortController }[]
  >([])
  // a run parked at a step boundary; Stop still works from here
  const [paused, setPaused] = useState(false)
  // tool names trusted via "Always allow" / Settings - kept here so the
  // approval card can add to the list without a Settings round-trip
  const [autoTools, setAutoToolsList] = useState<string[]>([])
  useEffect(() => {
    getAutoTools()
      .then((r) => setAutoToolsList(r.tools))
      .catch(console.error)
  }, [])

  async function alwaysAllow(name: string) {
    if (autoTools.includes(name)) return
    try {
      const r = await setAutoTools([...autoTools, name])
      setAutoToolsList(r.tools)
    } catch (e) {
      console.error(e)
    }
  }
  // elapsed seconds for the run-status line; resets each run
  const [elapsed, setElapsed] = useState(0)
  // tokens used by the current run (not the whole conversation), for the
  // status line; usage events accumulate, each send resets
  const [runTokens, setRunTokens] = useState(0)
  useEffect(() => {
    if (!streaming) return
    const t0 = Date.now()
    setElapsed(0)
    const id = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - t0) / 1000)),
      1000,
    )
    return () => window.clearInterval(id)
  }, [streaming])

  // Esc stops the active run - reaching for the Stop button mid-runaway is
  // exactly when the mouse is hardest to find
  useEffect(() => {
    if (!streaming) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') abortRef.current?.abort()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [streaming])
  // busy covers rewind round-trips before streaming starts; busyRef is the
  // SYNCHRONOUS re-entrancy guard (React state lags awaits, so a double-click
  // would otherwise rewind twice and destroy an extra user turn)
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [detecting, setDetecting] = useState(false)
  const [detectMsg, setDetectMsg] = useState('')
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
        if (file.type.startsWith('image/') || file.type.startsWith('video/')) {
          // pixels, not text: stored server-side, the message carries the id
          const meta = await uploadMedia(file)
          setPending((prev) => [
            ...prev,
            { name: meta.name, content: '', chars: 0, truncated: false, media: meta },
          ])
        } else {
          const att = await uploadAttachment(file)
          setPending((prev) => [...prev, att])
        }
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

  // Follow the stream only while the user is already at the bottom. Scrolling
  // up means "hold still, I'm reading" - yanking the thread back down on every
  // token is what made agent runs feel jumpy. A floating button offers the
  // way back to the live edge.
  const stickRef = useRef(true)
  const [detached, setDetached] = useState(false)

  function handleScroll() {
    const el = scrollRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    stickRef.current = atBottom
    setDetached(!atBottom)
  }

  useEffect(() => {
    const el = scrollRef.current
    if (el && stickRef.current) el.scrollTop = el.scrollHeight
  }, [items])

  function jumpToLatest() {
    stickRef.current = true
    setDetached(false)
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }

  function handleEvent(ev: StreamEvent) {
    if (ev.type === 'meta' && ev.conversation_id) {
      if (!convIdRef.current) {
        convIdRef.current = ev.conversation_id
        onConversationCreated(ev.conversation_id)
      }
      return
    }
    if (ev.type === 'paused') setPaused(true)
    if (ev.type === 'resumed') setPaused(false)
    if (ev.type === 'usage') {
      setRunTokens((t) => t + (ev.input_tokens ?? 0) + (ev.output_tokens ?? 0))
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
    streamStarted()
    setRunTokens(0)
    setPaused(false)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      await streamChat(req, handleEvent, controller.signal)
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        setItems((prev) => [...prev, { kind: 'error', content: String(e) }])
      }
    } finally {
      streamEnded()
      setPaused(false)
      abortRef.current = null
      onStreamEnd()
    }
  }

  /** A message sent while a run is active: wait server-side for the run to
   *  finish, then stream as usual. Cancelling the chip aborts the wait. */
  function queueFollowUp(message: string) {
    const controller = new AbortController()
    const id = Date.now() + Math.random()
    setQueued((q) => [...q, { id, text: message, controller }])
    streamStarted()
    ;(async () => {
      try {
        await streamChat(
          {
            conversation_id: convIdRef.current ?? undefined,
            provider_id: providerId,
            model,
            message,
            agent_mode: agentMode,
            auto_approve: autoMode,
            params: cleanParams(),
            queue: true,
          },
          handleEvent,
          controller.signal,
        )
      } catch (e) {
        if ((e as Error).name !== 'AbortError') {
          setItems((prev) => [...prev, { kind: 'error', content: String(e) }])
        }
      } finally {
        setQueued((q) => q.filter((x) => x.id !== id))
        streamEnded()
        onStreamEnd()
      }
    })()
  }

  async function send() {
    const typed = input.trim()
    // a run is going: queue the follow-up instead of refusing it. Attachments
    // stay out of the queue - the chip has no way to show what was attached,
    // and by fire time the context may have changed anyway
    if (streaming) {
      if (!typed || pending.length > 0 || uploading || !providerId || !model || !convIdRef.current) return
      setInput('')
      queueFollowUp(typed)
      return
    }
    if ((!typed && pending.length === 0) || busyRef.current || uploading || !providerId || !model) return
    // text attachments ride inside the message body; media attachments go by
    // id and are stored on the message row so later turns can resend pixels
    const textAtts = pending.filter((a) => !a.media)
    const mediaRefs = pending.flatMap((a) => (a.media ? [a.media] : []))
    const message = [typed, ...textAtts.map(fileBlock)].filter(Boolean).join('\n\n')
    setInput('')
    setPending([])
    setIsResearch(false) // a normal turn makes this an ordinary conversation
    // sending always returns to the live edge, wherever the user had scrolled
    stickRef.current = true
    setDetached(false)
    setItems((prev) => [
      ...prev,
      { kind: 'user', content: message, ...(mediaRefs.length ? { media: mediaRefs } : {}) },
    ])
    await runStream({
      conversation_id: convIdRef.current ?? undefined,
      provider_id: providerId,
      model,
      message,
      agent_mode: agentMode,
      auto_approve: autoMode,
      attachments: mediaRefs.length ? mediaRefs.map((m) => m.id) : undefined,
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
    streamStarted()
    setRunTokens(0)
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
      streamEnded()
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
        auto_approve: autoMode,
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
      // media comes back as pending chips with their store ids intact, so
      // resending attaches the same files instead of re-uploading
      setPending([
        ...files.map((f) => ({
          name: f.name, content: f.content, chars: f.content.length, truncated: false,
        })),
        ...(res.removed_user_attachments ?? []).map((m) => ({
          name: m.name, content: '', chars: 0, truncated: false,
          media: { ...m, url: mediaUrl(m.id) },
        })),
      ])
      textareaRef.current?.focus()
    } catch (e) {
      setItems((prev) => [...prev, { kind: 'error', content: String(e) }])
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  async function detectBackends() {
    setDetecting(true)
    setDetectMsg('')
    try {
      const res = await detectProviders()
      if (res.added.length) {
        setDetectMsg(`Found ${res.added.map((p) => p.name).join(' and ')}.`)
        onProvidersChanged()
      } else {
        // "nothing added" has three causes and this used to report the least
        // likely one. The server now says which, so a backend that is running
        // but has no models pulled is told to pull one rather than to start
        // the thing it can already see running.
        setDetectMsg(res.hint)
      }
    } catch (e) {
      setDetectMsg(String(e))
    } finally {
      setDetecting(false)
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
  const toolCount = items.filter((it) => it.kind === 'tool').length
  const lastTool = [...items].reverse().find((it): it is ToolItem => it.kind === 'tool')
  const runActivity = paused
    ? 'paused'
    : lastTool?.status === 'awaiting_approval'
      ? `waiting for approval: ${lastTool.call.name}`
      : lastTool?.status === 'running'
        ? `running ${lastTool.call.name}`
        : 'thinking'
  const elapsedLabel =
    elapsed >= 60 ? `${Math.floor(elapsed / 60)}m ${elapsed % 60}s` : `${elapsed}s`
  const lastUserIndex = items.map((it) => it.kind).lastIndexOf('user')
  const canRewind =
    Boolean(convIdRef.current) && !streaming && !busy && canSend && lastUserIndex >= 0 &&
    !isResearch

  return (
    <div className="chat">
      <div className="thread" ref={scrollRef} onScroll={handleScroll}>
        {items.length === 0 && (
          <div className="thread-empty">
            <div className="thread-empty-logo">👁</div>
            <h2>Syrudas AI</h2>
            <p>
              {canSend
                ? agentMode
                  ? autoMode
                    ? 'Auto mode: the model plans and uses tools on its own — shell commands, web fetches and writes outside the workspace run without asking.'
                    : 'Agent mode: the model can plan and use tools. Shell commands, web fetches and writes outside the workspace wait for your approval.'
                  : 'Ask anything. Swap models any time from the picker above.'
                : (detectMsg ||
                    'No model backend yet. Start Ollama or LM Studio, then look again — or add a provider by hand in Settings.')}
            </p>
            {!canSend && (
              <div className="setup-actions">
                <button className="btn btn-primary" disabled={detecting} onClick={detectBackends}>
                  {detecting ? 'Looking…' : 'Look for local backends'}
                </button>
                <button className="btn" onClick={onOpenSettings}>
                  Open Settings
                </button>
                {detectMsg && <p className="setup-msg">{detectMsg}</p>}
              </div>
            )}
          </div>
        )}
        {groupToolRuns(items).map((block, i) => {
          // a run of 2+ adjacent tool calls collapses into one block; a lone
          // call renders exactly as before
          if (block.kind === 'tool-group' && block.items.length > 1) {
            return (
              <ToolGroup
                key={i}
                items={block.items}
                onResolved={(it, approved) =>
                  it.approvalId && markToolResolved(it.approvalId, approved)
                }
                onAlwaysAllow={alwaysAllow}
              />
            )
          }
          const item = block.kind === 'tool-group' ? block.items[0] : block
          switch (item.kind) {
            case 'user': {
              const { text, files } = parseUserContent(item.content)
              return (
                <div key={i} className="msg user">
                  <div className="bubble">
                    {item.media && item.media.length > 0 && (
                      <div className="bubble-media">
                        {item.media.map((m, mi) =>
                          m.kind === 'image' ? (
                            <a key={mi} href={mediaUrl(m.id)} target="_blank" rel="noreferrer">
                              <img className="bubble-image" src={mediaUrl(m.id)} alt={m.name} />
                            </a>
                          ) : (
                            <video
                              key={mi}
                              className="bubble-video"
                              controls
                              preload="metadata"
                              poster={`${mediaUrl(m.id)}?frame=1`}
                              src={mediaUrl(m.id)}
                            />
                          ),
                        )}
                      </div>
                    )}
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
                  onAlwaysAllow={alwaysAllow}
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
        {streaming && (() => {
          // show the model is thinking right after sending, and between agent
          // steps (last tool finished, next model turn not yet streaming) -
          // those gaps used to look like the run had died
          const last = items[items.length - 1]
          const waitingOnModel =
            last?.kind === 'user' ||
            (last?.kind === 'tool' && last.status !== 'running' && last.status !== 'awaiting_approval')
          return waitingOnModel ? <div className="msg assistant thinking">…</div> : null
        })()}
        {canRewind && canSend && (
          <div className="thread-actions">
            <button className="btn btn-compact" title="Delete the last reply and generate a new one" onClick={regenerate}>
              ↻ Regenerate
            </button>
          </div>
        )}
      </div>
      {detached && items.length > 0 && (
        <button
          className="jump-latest"
          title="Scroll back to the newest message"
          aria-label="Scroll back to the newest message"
          onClick={jumpToLatest}
        >
          ↓ {streaming ? 'following paused — back to the latest' : 'back to the latest'}
        </button>
      )}
      {streaming && (
        <div className="run-status" role="status" aria-live="polite">
          <span className="run-dot" />
          working · {elapsedLabel}
          {toolCount > 0 && ` · ${toolCount} tool call${toolCount === 1 ? '' : 's'}`}
          {runTokens > 0 && ` · ${runTokens.toLocaleString()} tokens`}
          {` · ${runActivity}`}
        </div>
      )}
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
        {queued.length > 0 && (
          <div className="pending-files">
            {queued.map((q) => (
              <span key={q.id} className="pending-chip" title={q.text}>
                ⏳ queued: {q.text.slice(0, 40)}
                {q.text.length > 40 ? '…' : ''}
                <button
                  className="icon-btn"
                  title="Cancel this queued message"
                  aria-label="Cancel this queued message"
                  onClick={() => {
                    q.controller.abort()
                    setQueued((prev) => prev.filter((x) => x.id !== q.id))
                  }}
                >
                  ✕
                </button>
              </span>
            ))}
          </div>
        )}
        {(pending.length > 0 || uploading) && (
          <div className="pending-files">
            {pending.map((a, i) => (
              <span
                key={i}
                className="pending-chip"
                title={a.media ? `${a.media.mime} · ${a.name}` : `${a.chars.toLocaleString()} chars`}
              >
                {a.media ? (
                  a.media.kind === 'image' ? (
                    <>
                      <img className="pending-thumb" src={a.media.url} alt="" />
                      {a.name}
                    </>
                  ) : (
                    <>
                      🎬 {a.name}
                      {a.media.frames ? ` (${a.media.frames} frames)` : ''}
                    </>
                  )
                ) : (
                  <>
                    📎 {a.name}
                    {a.truncated ? ' (truncated)' : ''}
                  </>
                )}
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
            title="Attach files (text, code, CSV, JSON, PDF, images, video) - drag & drop or paste works too"
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
            onPaste={(e) => {
              // a copied screenshot lands as a file, not text - attach it
              // instead of pasting a useless filename or nothing at all
              if (e.clipboardData.files.length && canSend && !streaming) {
                e.preventDefault()
                addFiles(e.clipboardData.files)
              }
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                send()
              }
            }}
          />
          {streaming ? (
            <>
              {agentMode && convIdRef.current && (
                <button
                  className="btn"
                  title={
                    paused
                      ? 'Resume the paused run'
                      : 'Pause after the current step - the run parks until you resume or stop it'
                  }
                  onClick={() => {
                    const id = convIdRef.current
                    if (!id) return
                    if (paused) resumeRun(id).catch(console.error)
                    else pauseRun(id).catch(console.error)
                  }}
                >
                  {paused ? '▶ Resume' : '⏸ Pause'}
                </button>
              )}
              <button
                className="btn btn-danger"
                title="Stop the run (Esc)"
                onClick={() => abortRef.current?.abort()}
              >
                Stop
              </button>
              <button
                className="btn btn-primary"
                title="Send this when the current run finishes"
                disabled={!canSend || uploading || !input.trim() || pending.length > 0}
                onClick={send}
              >
                Queue
              </button>
            </>
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
