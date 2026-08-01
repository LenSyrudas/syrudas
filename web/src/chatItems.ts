/** The chat thread's data model and the pure transforms over it.
 *
 *  Kept out of the component so the stream-event reducer and the history
 *  rebuild — the two places where an off-by-one turn shows the user the wrong
 *  thing — can be unit tested without a browser.
 */
import type { Conversation, StreamEvent, ToolCall } from './types'

export interface ToolItem {
  kind: 'tool'
  call: ToolCall
  result?: string
  status: 'running' | 'done' | 'awaiting_approval' | 'denied' | 'error'
  approvalId?: string
}

/** Mirrors `tool_result_failed` in server/chat.py.
 *
 *  Tools report failure by returning a string starting with "Error:" — there is
 *  no separate channel — so classification is by convention. The live stream
 *  carries the server's verdict as `is_error`; this is the same rule applied to
 *  a row read back from storage, so the two cannot disagree about one result.
 */
export function statusForResult(content: string): ToolItem['status'] {
  const text = content.trimStart()
  if (text === DENIED_TOOL_RESULT) return 'denied'
  if (text.startsWith('Error:') || text.startsWith('[interrupted')) return 'error'
  return 'done'
}

export const DENIED_TOOL_RESULT = 'The user denied this tool call.'

/** Remove the tool-output fence from text the model wrote.
 *
 *  Results are wrapped in `<<<TOOL_OUTPUT name BEGIN/END>>>` so the model can
 *  tell data from instructions, and smaller models then imitate the syntax in
 *  their own replies. Harmless — a model emitting the marker gains nothing —
 *  but it is our punctuation leaking into the user's answer, so it is stripped
 *  for display only. Applied to the accumulated string rather than each delta,
 *  so a marker split across two chunks is still caught.
 */
const FENCE_RE = /<<<\s*TOOL_OUTPUT\b[^>]*>>>/gi

export function stripFence(text: string): string {
  return text.includes('<<<') ? text.replace(FENCE_RE, '') : text
}

export interface ResearchItem {
  kind: 'research'
  phase: string
  steps: string[]
  done: boolean
}

export interface Usage {
  input?: number
  output?: number
}

export type ChatItem =
  | { kind: 'user'; content: string }
  | { kind: 'assistant'; content: string; streaming?: boolean; usage?: Usage }
  | ToolItem
  | ResearchItem
  | { kind: 'error'; content: string }

// consumes the \n on BOTH sides that fileBlock() adds, or every edit/resend
// cycle would grow the attachment by one newline
const FILE_BLOCK = /<file name="([^"]*)">\n?([\s\S]*?)\n?<\/file>/g

/** Split a stored user message into typed text and attached-file blocks. */
export function parseUserContent(content: string): {
  text: string
  files: { name: string; content: string }[]
} {
  const files: { name: string; content: string }[] = []
  const text = content
    .replace(FILE_BLOCK, (_m, name: string, body: string) => {
      files.push({ name, content: body })
      return ''
    })
    .trim()
  return { text, files }
}

export function fileBlock(a: { name: string; content: string }): string {
  return `<file name="${a.name.replace(/"/g, "'")}">\n${a.content}\n</file>`
}

/** "1,234 in · 567 out" — omits either side the backend didn't report. */
export function usageLabel(u: Usage): string {
  const parts: string[] = []
  if (u.input != null) parts.push(`${u.input.toLocaleString()} in`)
  if (u.output != null) parts.push(`${u.output.toLocaleString()} out`)
  return parts.join(' · ')
}

/** Rebuild the thread from persisted messages (opening a conversation). */
export function itemsFromMessages(conv: Conversation): ChatItem[] {
  const loaded: ChatItem[] = []
  for (const m of conv.messages ?? []) {
    if (m.role === 'user') {
      loaded.push({ kind: 'user', content: m.content })
    } else if (m.role === 'assistant') {
      if (m.content) {
        // restore the persisted token counts so the readout survives a reload
        const usage: Usage | undefined =
          m.input_tokens != null || m.output_tokens != null
            ? { input: m.input_tokens ?? undefined, output: m.output_tokens ?? undefined }
            : undefined
        loaded.push({ kind: 'assistant', content: stripFence(m.content), usage })
      }
      for (const tc of m.tool_calls ?? []) {
        // 'running' until its result row is seen. Pushing 'done' here meant a
        // call that failed, was denied, or never finished at all came back from
        // storage wearing a green tick.
        loaded.push({ kind: 'tool', call: tc, status: 'running' })
      }
    } else if (m.role === 'tool') {
      const tool = loaded.find(
        (it): it is ToolItem =>
          it.kind === 'tool' && it.call.id === m.tool_call_id && it.result === undefined,
      )
      if (tool) {
        tool.result = m.content
        tool.status = statusForResult(m.content ?? '')
      }
    }
  }
  return loaded
}

/** Fold one stream event into the thread. Pure: returns a new array.
 *
 *  'meta' is deliberately NOT handled here — it carries no thread content and
 *  its effect (adopting a server-created conversation id) belongs to the
 *  component that owns that id.
 */
export function applyEvent(prev: ChatItem[], ev: StreamEvent): ChatItem[] {
  const next = [...prev]
  const last = next[next.length - 1]
  switch (ev.type) {
    case 'text_delta': {
      if (last?.kind === 'assistant' && last.streaming) {
        next[next.length - 1] = {
          ...last,
          content: stripFence(last.content + (ev.text ?? '')),
        }
      } else {
        next.push({ kind: 'assistant', content: stripFence(ev.text ?? ''), streaming: true })
      }
      break
    }
    case 'tool_call': {
      if (ev.tool_call) next.push({ kind: 'tool', call: ev.tool_call, status: 'running' })
      break
    }
    case 'research_status': {
      const line = ev.detail || ev.phase || ''
      if (last?.kind === 'research' && !last.done) {
        next[next.length - 1] = {
          ...last,
          phase: ev.phase ?? last.phase,
          steps: line ? [...last.steps, line] : last.steps,
        }
      } else {
        next.push({
          kind: 'research',
          phase: ev.phase ?? 'researching',
          steps: line ? [line] : [],
          done: false,
        })
      }
      break
    }
    case 'approval_required': {
      const idx = next.findIndex((it) => it.kind === 'tool' && it.call.id === ev.tool_call?.id)
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
          // the server classifies; fall back to the same rule so a live event
          // and the reloaded row can never disagree about the same result
          status:
            ev.denied || tool.status === 'denied'
              ? 'denied'
              : ev.is_error ?? false
                ? 'error'
                : statusForResult(ev.content ?? ''),
        }
      }
      break
    }
    case 'usage': {
      // The usage chunk arrives at the end of a step's stream, right after its
      // text — so it belongs to the turn still being generated (the last item,
      // while it's streaming). In agent mode a tool-only step produces no
      // assistant turn; drop its usage rather than misattribute it to an
      // earlier answer.
      if (ev.input_tokens == null && ev.output_tokens == null) break
      if (last?.kind === 'assistant' && last.streaming) {
        next[next.length - 1] = {
          ...last,
          usage: { input: ev.input_tokens, output: ev.output_tokens },
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
        if (it.kind === 'research' && !it.done) next[i] = { ...it, done: true }
      }
      break
    }
  }
  return next
}
