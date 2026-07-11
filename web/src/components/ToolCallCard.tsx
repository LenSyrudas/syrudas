import { useState } from 'react'
import { resolveApproval } from '../api'
import type { ToolItem } from './ChatView'

export default function ToolCallCard({
  item,
  onResolved,
}: {
  item: ToolItem
  onResolved: (approved: boolean) => void
}) {
  const [open, setOpen] = useState(item.status === 'awaiting_approval')

  const statusLabel = {
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
      {item.status === 'awaiting_approval' && item.approvalId && (
        <div className="tool-approval">
          <span>Allow this {item.call.name} call?</span>
          <button
            className="btn btn-primary"
            onClick={() => {
              resolveApproval(item.approvalId!, true).then(() => onResolved(true))
            }}
          >
            Approve
          </button>
          <button
            className="btn btn-danger"
            onClick={() => {
              resolveApproval(item.approvalId!, false).then(() => onResolved(false))
            }}
          >
            Deny
          </button>
        </div>
      )}
    </div>
  )
}
