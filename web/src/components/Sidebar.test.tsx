import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Sidebar from './Sidebar'
import type { Conversation } from '../types'
import * as api from '../api'

vi.mock('../api', () => ({
  deleteConversation: vi.fn(() => Promise.resolve({ ok: true })),
  patchConversation: vi.fn(() => Promise.resolve({} as Conversation)),
}))

const conv = (id: string, title: string, agent = 0): Conversation => ({
  id, title, provider_id: 'p', model: 'm', agent_mode: agent,
  system_prompt: '', created_at: '', updated_at: '',
})

const CONVS = [
  conv('1', 'Python code review'),
  conv('2', 'Dinner recipes'),
  conv('3', 'python packaging notes'),
]

function setup(props: Partial<React.ComponentProps<typeof Sidebar>> = {}) {
  const onSelect = vi.fn()
  const onRenamed = vi.fn()
  const onDeleted = vi.fn()
  render(
    <Sidebar
      conversations={CONVS}
      activeId={null}
      onSelect={onSelect}
      onNew={vi.fn()}
      onDeleted={onDeleted}
      onRenamed={onRenamed}
      onSettings={vi.fn()}
      onArena={vi.fn()}
      onEditor={vi.fn()}
      onCookbook={vi.fn()}
      settingsActive={false}
      arenaActive={false}
      editorActive={false}
      cookbookActive={false}
      {...props}
    />,
  )
  return { onSelect, onRenamed, onDeleted, user: userEvent.setup() }
}

const titles = () =>
  screen.queryAllByText((_, el) => el?.className === 'conv-title').map((e) => e.textContent?.trim())

beforeEach(() => {
  vi.clearAllMocks()
})

describe('Sidebar — conversation search', () => {
  it('lists every conversation when the box is empty', () => {
    setup()
    expect(titles()).toHaveLength(3)
  })

  it('filters to matching titles, case-insensitively', async () => {
    const { user } = setup()
    await user.type(screen.getByRole('searchbox', { name: /search conversations/i }), 'python')
    expect(titles()).toEqual(['Python code review', 'python packaging notes'])
  })

  it('explains when nothing matches instead of showing a blank list', async () => {
    const { user } = setup()
    await user.type(screen.getByRole('searchbox', { name: /search conversations/i }), 'zzz')
    expect(titles()).toHaveLength(0)
    expect(screen.getByText(/no conversations match/i)).toBeTruthy()
  })

  it('shows no search box at all when there are no conversations', () => {
    setup({ conversations: [] })
    expect(screen.queryByRole('searchbox')).toBeNull()
    expect(screen.getByText(/no conversations yet/i)).toBeTruthy()
  })

  it('ignores surrounding whitespace in the query', async () => {
    const { user } = setup()
    await user.type(screen.getByRole('searchbox', { name: /search conversations/i }), '  dinner  ')
    expect(titles()).toEqual(['Dinner recipes'])
  })
})

describe('Sidebar — rename', () => {
  async function startRename(user: ReturnType<typeof userEvent.setup>, title: string) {
    await user.click(screen.getByRole('button', { name: `Rename conversation ${title}` }))
    return screen.getByRole('textbox', { name: `Rename conversation ${title}` })
  }

  it('seeds the input with the current title and selects it', async () => {
    const { user } = setup()
    const input = await startRename(user, 'Dinner recipes')
    expect((input as HTMLInputElement).value).toBe('Dinner recipes')
    expect(document.activeElement).toBe(input)
  })

  it('saves on Enter and tells the parent to refresh', async () => {
    const { user, onRenamed } = setup()
    const input = await startRename(user, 'Dinner recipes')
    await user.clear(input)
    await user.type(input, 'Weeknight dinners{Enter}')
    expect(api.patchConversation).toHaveBeenCalledWith('2', { title: 'Weeknight dinners' })
    expect(onRenamed).toHaveBeenCalled()
  })

  it('saves on blur (clicking away)', async () => {
    const { user, onRenamed } = setup()
    const input = await startRename(user, 'Dinner recipes')
    await user.clear(input)
    await user.type(input, 'Renamed by blur')
    // click away, the way a user would, rather than calling blur() directly
    await user.click(screen.getByRole('searchbox', { name: /search conversations/i }))
    expect(api.patchConversation).toHaveBeenCalledWith('2', { title: 'Renamed by blur' })
    // onRenamed fires from the PATCH promise, so let the microtask flush
    await waitFor(() => expect(onRenamed).toHaveBeenCalled())
  })

  it('discards the edit on Escape', async () => {
    const { user, onRenamed } = setup()
    const input = await startRename(user, 'Dinner recipes')
    await user.clear(input)
    await user.type(input, 'THROW THIS AWAY{Escape}')
    expect(api.patchConversation).not.toHaveBeenCalled()
    expect(onRenamed).not.toHaveBeenCalled()
    expect(titles()).toContain('Dinner recipes')
  })

  it('does not write when the title is unchanged', async () => {
    const { user } = setup()
    const input = await startRename(user, 'Dinner recipes')
    await user.type(input, '{Enter}')
    expect(api.patchConversation).not.toHaveBeenCalled()
  })

  it('does not write a title that is blank or only whitespace', async () => {
    const { user } = setup()
    const input = await startRename(user, 'Dinner recipes')
    await user.clear(input)
    await user.type(input, '   {Enter}')
    expect(api.patchConversation).not.toHaveBeenCalled()
  })

  it('trims the saved title', async () => {
    const { user } = setup()
    const input = await startRename(user, 'Dinner recipes')
    await user.clear(input)
    await user.type(input, '  Spaced out  {Enter}')
    expect(api.patchConversation).toHaveBeenCalledWith('2', { title: 'Spaced out' })
  })

  // typing in the rename box must not reach the row's Enter/Space "open this
  // conversation" handler, or renaming would navigate away mid-edit
  it('does not open the conversation while typing in the rename box', async () => {
    const { user, onSelect } = setup()
    const input = await startRename(user, 'Dinner recipes')
    await user.clear(input)
    await user.type(input, 'a new name with spaces{Enter}')
    expect(onSelect).not.toHaveBeenCalled()
    expect((input as HTMLInputElement).value).toContain(' ')
  })
})

describe('Sidebar — selection and delete', () => {
  it('opens a conversation when its row is clicked', async () => {
    const { user, onSelect } = setup()
    await user.click(screen.getByText('Dinner recipes'))
    expect(onSelect).toHaveBeenCalledWith('2')
  })

  it('marks the active row for assistive tech', () => {
    setup({ activeId: '2' })
    const active = screen.getAllByRole('button').find((b) => b.getAttribute('aria-current') === 'true')
    expect(active && within(active).queryByText('Dinner recipes')).toBeTruthy()
  })

  it('deletes only after the confirm prompt is accepted', async () => {
    const { user, onDeleted } = setup()
    vi.stubGlobal('confirm', vi.fn(() => false))
    await user.click(screen.getByRole('button', { name: 'Delete conversation Dinner recipes' }))
    expect(api.deleteConversation).not.toHaveBeenCalled()

    vi.stubGlobal('confirm', vi.fn(() => true))
    await user.click(screen.getByRole('button', { name: 'Delete conversation Dinner recipes' }))
    expect(api.deleteConversation).toHaveBeenCalledWith('2')
    expect(onDeleted).toHaveBeenCalledWith('2')
    vi.unstubAllGlobals()
  })
})
