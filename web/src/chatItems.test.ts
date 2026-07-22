import { describe, expect, it } from 'vitest'
import {
  applyEvent,
  fileBlock,
  itemsFromMessages,
  parseUserContent,
  usageLabel,
} from './chatItems'
import type { ChatItem } from './chatItems'
import type { Conversation, DbMessage, StreamEvent } from './types'

/** Fold a whole event script, the way a real stream arrives. */
function run(events: StreamEvent[], start: ChatItem[] = []): ChatItem[] {
  return events.reduce(applyEvent, start)
}

const msg = (m: Partial<DbMessage>): DbMessage => ({
  id: 'm', role: 'user', content: '', tool_calls: null, tool_call_id: null,
  created_at: '2026-01-01', input_tokens: null, output_tokens: null, ...m,
})

const conv = (messages: DbMessage[]): Conversation => ({
  id: 'c', title: 't', provider_id: 'p', model: 'm', agent_mode: 0,
  system_prompt: '', created_at: '', updated_at: '', messages,
})

describe('applyEvent — text streaming', () => {
  it('starts an assistant turn and appends deltas into it', () => {
    const items = run([
      { type: 'text_delta', text: 'Hel' },
      { type: 'text_delta', text: 'lo' },
    ])
    expect(items).toHaveLength(1)
    expect(items[0]).toMatchObject({ kind: 'assistant', content: 'Hello', streaming: true })
  })

  it('starts a NEW turn when the previous one already finished', () => {
    const items = run([
      { type: 'text_delta', text: 'first' },
      { type: 'done' },
      { type: 'text_delta', text: 'second' },
    ])
    expect(items.map((i) => i.kind)).toEqual(['assistant', 'assistant'])
    expect(items[0]).toMatchObject({ content: 'first', streaming: false })
    expect(items[1]).toMatchObject({ content: 'second', streaming: true })
  })

  it('does not mutate the array it was given', () => {
    const before: ChatItem[] = [{ kind: 'user', content: 'hi' }]
    const after = applyEvent(before, { type: 'text_delta', text: 'x' })
    expect(before).toHaveLength(1)
    expect(after).toHaveLength(2)
  })

  it('done clears streaming and closes open research cards', () => {
    const items = run([
      { type: 'research_status', phase: 'searching', detail: 'step one' },
      { type: 'text_delta', text: 'report' },
      { type: 'done' },
    ])
    expect(items.find((i) => i.kind === 'research')).toMatchObject({ done: true })
    expect(items.find((i) => i.kind === 'assistant')).toMatchObject({ streaming: false })
  })
})

describe('applyEvent — token usage attribution', () => {
  it('attaches counts to the turn being streamed', () => {
    const items = run([
      { type: 'text_delta', text: 'hi' },
      { type: 'usage', input_tokens: 553, output_tokens: 70 },
    ])
    expect(items[0]).toMatchObject({ usage: { input: 553, output: 70 } })
  })

  it('keeps the counts through done', () => {
    const items = run([
      { type: 'text_delta', text: 'hi' },
      { type: 'usage', input_tokens: 5, output_tokens: 6 },
      { type: 'done' },
    ])
    expect(items[0]).toMatchObject({ streaming: false, usage: { input: 5, output: 6 } })
  })

  // the regression this reducer was extracted for: an agent step that only
  // calls a tool produces no assistant turn, and its usage must NOT be written
  // onto the previous answer
  it('drops usage from a tool-only agent step instead of misattributing it', () => {
    const items = run([
      { type: 'text_delta', text: 'let me check' },
      { type: 'usage', input_tokens: 100, output_tokens: 10 },
      { type: 'tool_call', tool_call: { id: 't1', name: 'shell', arguments: {} } },
      { type: 'tool_result', tool_call_id: 't1', content: 'ok' },
      // step 2: tool-only, no text_delta at all
      { type: 'usage', input_tokens: 999, output_tokens: 999 },
    ])
    const assistant = items.find((i) => i.kind === 'assistant')
    expect(assistant).toMatchObject({ usage: { input: 100, output: 10 } })
  })

  it('gives each agent text turn its own step counts', () => {
    const items = run([
      { type: 'text_delta', text: 'thinking' },
      { type: 'usage', input_tokens: 10, output_tokens: 1 },
      { type: 'tool_call', tool_call: { id: 't1', name: 'shell', arguments: {} } },
      { type: 'tool_result', tool_call_id: 't1', content: 'done' },
      { type: 'text_delta', text: 'the answer' },
      { type: 'usage', input_tokens: 20, output_tokens: 2 },
      { type: 'done' },
    ])
    const turns = items.filter((i) => i.kind === 'assistant')
    expect(turns).toHaveLength(2)
    expect(turns[0]).toMatchObject({ usage: { input: 10, output: 1 } })
    expect(turns[1]).toMatchObject({ usage: { input: 20, output: 2 } })
  })

  it('ignores a usage event carrying no numbers', () => {
    const items = run([{ type: 'text_delta', text: 'hi' }, { type: 'usage' }])
    expect(items[0]).not.toHaveProperty('usage')
  })

  it('records a one-sided count when only one side is reported', () => {
    const items = run([
      { type: 'text_delta', text: 'hi' },
      { type: 'usage', output_tokens: 42 },
    ])
    expect(items[0]).toMatchObject({ usage: { input: undefined, output: 42 } })
  })

  it('never attaches usage when no turn is streaming', () => {
    const items = run([{ type: 'usage', input_tokens: 5, output_tokens: 5 }])
    expect(items).toHaveLength(0)
  })
})

describe('applyEvent — tool calls and approval', () => {
  it('pairs a result with its call and marks it done', () => {
    const items = run([
      { type: 'tool_call', tool_call: { id: 'a', name: 'read_file', arguments: {} } },
      { type: 'tool_result', tool_call_id: 'a', content: 'file body' },
    ])
    expect(items[0]).toMatchObject({ kind: 'tool', status: 'done', result: 'file body' })
  })

  it('pairs with the FIRST unresolved call of that id, not a resolved one', () => {
    const items = run([
      { type: 'tool_call', tool_call: { id: 'dup', name: 'x', arguments: {} } },
      { type: 'tool_result', tool_call_id: 'dup', content: 'first' },
      { type: 'tool_call', tool_call: { id: 'dup', name: 'x', arguments: {} } },
      { type: 'tool_result', tool_call_id: 'dup', content: 'second' },
    ])
    const tools = items.filter((i) => i.kind === 'tool')
    expect(tools.map((t) => (t as { result?: string }).result)).toEqual(['first', 'second'])
  })

  it('flips an existing call to awaiting_approval rather than duplicating it', () => {
    const items = run([
      { type: 'tool_call', tool_call: { id: 's1', name: 'shell', arguments: {} } },
      {
        type: 'approval_required',
        tool_call: { id: 's1', name: 'shell', arguments: {} },
        approval_id: 'ap1',
      },
    ])
    expect(items).toHaveLength(1)
    expect(items[0]).toMatchObject({ status: 'awaiting_approval', approvalId: 'ap1' })
  })

  it('a denied call stays denied when its result arrives', () => {
    const denied: ChatItem[] = [
      { kind: 'tool', call: { id: 'd', name: 'shell', arguments: {} }, status: 'denied' },
    ]
    const items = applyEvent(denied, {
      type: 'tool_result', tool_call_id: 'd', content: 'The user denied this tool call.',
    })
    expect(items[0]).toMatchObject({ status: 'denied' })
  })

  it('ignores a result for a tool call it never saw', () => {
    const items = run([{ type: 'tool_result', tool_call_id: 'ghost', content: 'x' }])
    expect(items).toHaveLength(0)
  })
})

describe('applyEvent — research and errors', () => {
  it('accumulates steps into one open research card', () => {
    const items = run([
      { type: 'research_status', phase: 'planning', detail: 'a' },
      { type: 'research_status', phase: 'searching', detail: 'b' },
    ])
    expect(items).toHaveLength(1)
    expect(items[0]).toMatchObject({ kind: 'research', phase: 'searching', steps: ['a', 'b'] })
  })

  it('surfaces an error as its own item with a fallback message', () => {
    const items = run([{ type: 'error' }])
    expect(items[0]).toMatchObject({ kind: 'error', content: 'Unknown error' })
  })
})

describe('itemsFromMessages — rebuilding a saved conversation', () => {
  it('restores persisted token counts onto the assistant turn', () => {
    const items = itemsFromMessages(
      conv([
        msg({ role: 'user', content: 'q' }),
        msg({ role: 'assistant', content: 'a', input_tokens: 553, output_tokens: 70 }),
      ]),
    )
    expect(items[1]).toMatchObject({ kind: 'assistant', usage: { input: 553, output: 70 } })
  })

  it('leaves usage undefined for a message stored before counts were persisted', () => {
    const items = itemsFromMessages(conv([msg({ role: 'assistant', content: 'old' })]))
    // the key may be present; what matters is that it is falsy, since that is
    // what the render guard tests before showing the readout
    expect((items[0] as { usage?: unknown }).usage).toBeUndefined()
  })

  it('keeps a zero count rather than discarding it as falsy', () => {
    const items = itemsFromMessages(
      conv([msg({ role: 'assistant', content: 'a', input_tokens: 0, output_tokens: 0 })]),
    )
    expect(items[0]).toMatchObject({ usage: { input: 0, output: 0 } })
  })

  it('pairs stored tool results with their calls', () => {
    const items = itemsFromMessages(
      conv([
        msg({ role: 'user', content: 'run it' }),
        msg({
          role: 'assistant', content: 'sure',
          tool_calls: [{ id: 't1', name: 'shell', arguments: { cmd: 'ls' } }],
        }),
        msg({ role: 'tool', content: 'output', tool_call_id: 't1' }),
      ]),
    )
    expect(items.map((i) => i.kind)).toEqual(['user', 'assistant', 'tool'])
    expect(items[2]).toMatchObject({ kind: 'tool', status: 'done', result: 'output' })
  })

  it('omits an assistant turn that carried only a tool call', () => {
    const items = itemsFromMessages(
      conv([
        msg({ role: 'assistant', content: '', tool_calls: [{ id: 'x', name: 'n', arguments: {} }] }),
      ]),
    )
    expect(items.map((i) => i.kind)).toEqual(['tool'])
  })

  it('handles a conversation with no messages', () => {
    expect(itemsFromMessages(conv([]))).toEqual([])
  })
})

describe('parseUserContent / fileBlock', () => {
  it('round-trips an attachment without accumulating newlines', () => {
    const att = { name: 'a.py', content: 'print(1)' }
    let stored = ['hello', fileBlock(att)].join('\n\n')
    // three edit/resend cycles must not grow the body
    for (let i = 0; i < 3; i++) {
      const parsed = parseUserContent(stored)
      expect(parsed.text).toBe('hello')
      expect(parsed.files[0].content).toBe('print(1)')
      stored = [parsed.text, fileBlock(parsed.files[0])].join('\n\n')
    }
  })

  it('extracts several files and leaves the typed text behind', () => {
    const stored = [
      'look at these',
      fileBlock({ name: 'a.txt', content: 'A' }),
      fileBlock({ name: 'b.txt', content: 'B' }),
    ].join('\n\n')
    const { text, files } = parseUserContent(stored)
    expect(text).toBe('look at these')
    expect(files.map((f) => [f.name, f.content])).toEqual([['a.txt', 'A'], ['b.txt', 'B']])
  })

  it('escapes a quote in the filename so the block stays parseable', () => {
    const block = fileBlock({ name: 'we"ird.txt', content: 'x' })
    const { files } = parseUserContent(block)
    expect(files).toHaveLength(1)
    expect(files[0].content).toBe('x')
  })

  it('preserves multi-line content exactly', () => {
    const body = 'line1\n\nline3'
    const { files } = parseUserContent(fileBlock({ name: 'm.txt', content: body }))
    expect(files[0].content).toBe(body)
  })

  it('returns plain text unchanged when there are no attachments', () => {
    expect(parseUserContent('just words')).toEqual({ text: 'just words', files: [] })
  })
})

describe('usageLabel', () => {
  it('formats both sides with thousands separators', () => {
    expect(usageLabel({ input: 1234, output: 567 })).toBe('1,234 in · 567 out')
  })

  it('omits a side the backend did not report', () => {
    expect(usageLabel({ output: 42 })).toBe('42 out')
    expect(usageLabel({ input: 42 })).toBe('42 in')
  })

  it('renders zero rather than dropping it', () => {
    expect(usageLabel({ input: 0, output: 0 })).toBe('0 in · 0 out')
  })

  it('is empty when nothing was reported, so the caller can hide it', () => {
    expect(usageLabel({})).toBe('')
  })
})
