import { useEffect, useState } from 'react'
import { resolveApproval } from '../api'
import type { ToolItem } from '../chatItems'

export default function ToolCallCard({
  item,
  onResolved,
  onAlwaysAllow,
}: {
  item: ToolItem
  onResolved: (approved: boolean) => void
  /** trust this tool name from now on (Settings → Agent auto-approve) */
  onAlwaysAllow?: (toolName: string) => void
}) {
  const [open, setOpen] = useState(item.status === 'awaiting_approval')

  const awaiting = item.status === 'awaiting_approval' && item.approvalId
  const decide = (approved: boolean) => {
    if (!item.approvalId) return
    resolveApproval(item.approvalId, approved).then(() => onResolved(approved))
  }

  // Y approves, N denies - but never while the user is typing in the composer
  useEffect(() => {
    if (!awaiting) return
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement
      if (el && (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT')) return
      if (e.key === 'y' || e.key === 'Y') decide(true)
      if (e.key === 'n' || e.key === 'N') decide(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [awaiting, item.approvalId])

  const statusLabel =
    item.autoApproved && item.status === 'done'
      ? '⚡ auto'
      : {
          running: '⏳ running',
          done: '✓ done',
          awaiting_approval: '⏸ needs approval',
          denied: '✗ denied',
          error: '✗ error',
        }[item.status]

  return (
    <div className={`tool-card ${item.status}`}>
      <div className="tool-head" onClick={() => setOpen(!open)}>
        <span className="tool-name">🔧 {item.call.name}</span>
        <span className={`tool-status ${item.status}`}>{statusLabel}</span>
      </div>
      {open && (
        <div className="tool-body">
          <div className="tool-section">
            <div className="tool-label">input</div>
            <pre>{JSON.stringify(item.call.arguments, null, 2)}</pre>
          </div>
          {item.result !== undefined && (
            <div className="tool-section">
              <div className="tool-label">output</div>
              <pre>{item.result}</pre>
            </div>
          )}
        </div>
      )}
      {awaiting && (
        <div className="tool-approval">
          <span>
            Allow this {item.call.name} call? <span className="muted">(Y / N)</span>
          </span>
          <button className="btn btn-primary" onClick={() => decide(true)}>
            Approve
          </button>
          <button className="btn btn-danger" onClick={() => decide(false)}>
            Deny
          </button>
          {onAlwaysAllow && (
            <button
              className="btn"
              title={`Approve now and stop asking for ${item.call.name} - reversible in Settings → Agent auto-approve`}
              onClick={() => {
                onAlwaysAllow(item.call.name)
                decide(true)
              }}
            >
              Always allow {item.call.name}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
