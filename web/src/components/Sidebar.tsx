import { deleteConversation } from '../api'
import type { Conversation } from '../types'

interface Props {
  conversations: Conversation[]
  activeId: string | null
  onSelect: (id: string) => void
  onNew: () => void
  onDeleted: (id: string) => void
  onSettings: () => void
  settingsActive: boolean
}

export default function Sidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDeleted,
  onSettings,
  settingsActive,
}: Props) {
  return (
    <aside className="sidebar">
      <div className="sidebar-head">
        <span className="logo">👁 Syrudas AI</span>
        <button className="btn btn-primary" onClick={onNew}>
          + New chat
        </button>
      </div>
      <nav className="conv-list">
        {conversations.map((c) => (
          <div
            key={c.id}
            className={`conv-item ${c.id === activeId && !settingsActive ? 'active' : ''}`}
            onClick={() => onSelect(c.id)}
          >
            <span className="conv-title" title={c.title}>
              {c.agent_mode ? '🛠 ' : ''}
              {c.title}
            </span>
            <button
              className="icon-btn conv-delete"
              title="Delete conversation"
              onClick={(e) => {
                e.stopPropagation()
                if (confirm(`Delete "${c.title}"?`)) {
                  deleteConversation(c.id).then(() => onDeleted(c.id))
                }
              }}
            >
              ✕
            </button>
          </div>
        ))}
        {conversations.length === 0 && <div className="conv-empty">No conversations yet</div>}
      </nav>
      <div className="sidebar-foot">
        <button className={`btn btn-ghost ${settingsActive ? 'active' : ''}`} onClick={onSettings}>
          ⚙ Settings
        </button>
      </div>
    </aside>
  )
}
