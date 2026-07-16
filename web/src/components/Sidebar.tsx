import { useEffect, useRef, useState } from 'react'
import { deleteConversation, patchConversation } from '../api'
import type { Conversation } from '../types'
import { setAppearance, THEME_EVENT } from '../theme'

function ThemeToggle() {
  const isDark = () => document.documentElement.getAttribute('data-theme') !== 'light'
  const [dark, setDark] = useState(isDark)
  // re-sync if the theme is changed elsewhere (Settings, OS in system mode)
  useEffect(() => {
    const onChange = () => setDark(isDark())
    window.addEventListener(THEME_EVENT, onChange)
    return () => window.removeEventListener(THEME_EVENT, onChange)
  }, [])
  return (
    <button
      className="btn btn-ghost theme-toggle"
      title={dark ? 'Switch to light theme' : 'Switch to dark theme'}
      aria-label={dark ? 'Switch to light theme' : 'Switch to dark theme'}
      // read the live attribute (not lagged state) so rapid clicks stay correct
      onClick={() => setAppearance(isDark() ? 'light' : 'dark')}
    >
      {dark ? '☀' : '🌙'}
    </button>
  )
}

interface Props {
  conversations: Conversation[]
  activeId: string | null
  onSelect: (id: string) => void
  onNew: () => void
  onDeleted: (id: string) => void
  onRenamed: () => void
  onSettings: () => void
  onArena: () => void
  onEditor: () => void
  onCookbook: () => void
  settingsActive: boolean
  arenaActive: boolean
  editorActive: boolean
  cookbookActive: boolean
}

export default function Sidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDeleted,
  onRenamed,
  onSettings,
  onArena,
  onEditor,
  onCookbook,
  settingsActive,
  arenaActive,
  editorActive,
  cookbookActive,
}: Props) {
  const [query, setQuery] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  // Escape cancels via a flag the shared blur-commit handler reads, so both
  // Enter/blur (save) and Esc (discard) funnel through one commit path.
  const cancelRef = useRef(false)

  const q = query.trim().toLowerCase()
  const shown = q
    ? conversations.filter((c) => c.title.toLowerCase().includes(q))
    : conversations

  function startRename(c: Conversation) {
    cancelRef.current = false
    setDraft(c.title)
    setEditingId(c.id)
  }

  function commitRename(c: Conversation) {
    const cancelled = cancelRef.current
    cancelRef.current = false
    setEditingId(null)
    if (cancelled) return
    const title = draft.trim()
    if (!title || title === c.title) return
    patchConversation(c.id, { title })
      .then(onRenamed)
      .catch((err) => alert(String(err)))
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-head">
        <span className="logo">👁 Syrudas AI</span>
        <button className="btn btn-primary" onClick={onNew}>
          + New chat
        </button>
        {conversations.length > 0 && (
          <input
            className="conv-search"
            type="search"
            placeholder="Search conversations…"
            aria-label="Search conversations"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        )}
      </div>
      <nav className="conv-list">
        {shown.map((c) => (
          <div
            key={c.id}
            className={`conv-item ${c.id === activeId && !settingsActive ? 'active' : ''}`}
            role="button"
            tabIndex={0}
            aria-current={c.id === activeId && !settingsActive}
            onClick={() => onSelect(c.id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onSelect(c.id)
              }
            }}
          >
            {editingId === c.id ? (
              <input
                className="conv-rename-input"
                value={draft}
                autoFocus
                aria-label={`Rename conversation ${c.title}`}
                onFocus={(e) => e.currentTarget.select()}
                onChange={(e) => setDraft(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                onKeyDown={(e) => {
                  e.stopPropagation()
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    e.currentTarget.blur()
                  } else if (e.key === 'Escape') {
                    e.preventDefault()
                    cancelRef.current = true
                    e.currentTarget.blur()
                  }
                }}
                onBlur={() => commitRename(c)}
              />
            ) : (
              <>
                <span className="conv-title" title={c.title}>
                  {c.agent_mode ? '🛠 ' : ''}
                  {c.title}
                </span>
                <button
                  className="icon-btn conv-rename"
                  title="Rename conversation"
                  aria-label={`Rename conversation ${c.title}`}
                  onClick={(e) => {
                    e.stopPropagation()
                    startRename(c)
                  }}
                >
                  ✎
                </button>
                <button
                  className="icon-btn conv-delete"
                  title="Delete conversation"
                  aria-label={`Delete conversation ${c.title}`}
                  onClick={(e) => {
                    e.stopPropagation()
                    if (confirm(`Delete "${c.title}"?`)) {
                      deleteConversation(c.id)
                        .then(() => onDeleted(c.id))
                        .catch((err) => alert(String(err)))
                    }
                  }}
                >
                  ✕
                </button>
              </>
            )}
          </div>
        ))}
        {conversations.length === 0 && <div className="conv-empty">No conversations yet</div>}
        {conversations.length > 0 && shown.length === 0 && (
          <div className="conv-empty">No conversations match “{query.trim()}”</div>
        )}
      </nav>
      <div className="sidebar-foot">
        <button className={`btn btn-ghost ${cookbookActive ? 'active' : ''}`} onClick={onCookbook}>
          📖 Cookbook
        </button>
        <button className={`btn btn-ghost ${editorActive ? 'active' : ''}`} onClick={onEditor}>
          ✍ Editor
        </button>
        <button className={`btn btn-ghost ${arenaActive ? 'active' : ''}`} onClick={onArena}>
          ⚔ Arena
        </button>
        <button className={`btn btn-ghost ${settingsActive ? 'active' : ''}`} onClick={onSettings}>
          ⚙ Settings
        </button>
        <ThemeToggle />
      </div>
    </aside>
  )
}
