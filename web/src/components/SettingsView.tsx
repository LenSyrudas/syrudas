import { useEffect, useState } from 'react'
import {
  addMemory,
  checkProvider,
  clearMemories,
  createMcpServer,
  createProvider,
  deleteMcpServer,
  deleteMemory,
  deleteProvider,
  getAgentFolders,
  listMcpServers,
  listMemories,
  listProviderTypes,
  listProviders,
  setAgentFolders,
  setMcpServerEnabled,
  updateProvider,
} from '../api'
import type { AgentFolders, MemoryEntry } from '../api'
import type { McpServer, ProviderInstance, ProviderType } from '../types'

export default function SettingsView({ onProvidersChanged }: { onProvidersChanged: () => void }) {
  const [version, setVersion] = useState('')
  useEffect(() => {
    fetch('/api/health')
      .then((r) => r.json())
      .then((h) => setVersion(h.version ?? ''))
      .catch(() => {})
  }, [])

  return (
    <div className="settings">
      <h1>Settings</h1>
      <ProvidersSection onChanged={onProvidersChanged} />
      <McpSection />
      <AgentAccessSection />
      <MemorySection />
      <footer className="settings-footer">
        👁 Syrudas AI{version ? ` v${version}` : ''} · local-first, no telemetry
      </footer>
    </div>
  )
}

function ProvidersSection({ onChanged }: { onChanged: () => void }) {
  const [types, setTypes] = useState<ProviderType[]>([])
  const [instances, setInstances] = useState<ProviderInstance[]>([])
  const [editing, setEditing] = useState<ProviderInstance | null>(null)
  const [adding, setAdding] = useState(false)
  const [checkResults, setCheckResults] = useState<Record<string, string>>({})

  const refresh = () => {
    listProviders().then(setInstances).catch(console.error)
    onChanged()
  }

  useEffect(() => {
    listProviderTypes().then(setTypes).catch(console.error)
    listProviders().then(setInstances).catch(console.error)
  }, [])

  return (
    <section className="settings-section">
      <div className="section-head">
        <h2>Model providers</h2>
        <button className="btn btn-primary" onClick={() => setAdding(true)}>
          + Add provider
        </button>
      </div>
      <p className="hint">
        A provider is a configured connection to a model backend. The OpenAI-compatible type works
        with Ollama (http://localhost:11434/v1), LM Studio (http://localhost:1234/v1), OpenRouter,
        OpenAI, vLLM and more. Drop new provider types into the <code>plugins/</code> folder.
      </p>
      {instances.map((inst) =>
        editing?.id === inst.id ? (
          <ProviderForm
            key={inst.id}
            types={types}
            initial={editing}
            onCancel={() => setEditing(null)}
            onSave={async (_typeId, name, config) => {
              await updateProvider(inst.id, name, config)
              setEditing(null)
              refresh()
            }}
          />
        ) : (
          <div key={inst.id} className="card row">
            <div className="grow">
              <strong>{inst.name}</strong>
              <div className="muted">
                {inst.type_id} · {inst.config.base_url ?? ''}
              </div>
              {checkResults[inst.id] && <div className="check-result">{checkResults[inst.id]}</div>}
            </div>
            <button
              className="btn"
              onClick={async () => {
                setCheckResults((r) => ({ ...r, [inst.id]: 'checking…' }))
                const res = await checkProvider(inst.id)
                setCheckResults((r) => ({
                  ...r,
                  [inst.id]: `${res.ok ? '✓' : '✗'} ${res.detail}`,
                }))
              }}
            >
              Test
            </button>
            <button className="btn" onClick={() => setEditing(inst)}>
              Edit
            </button>
            <button
              className="btn btn-danger"
              onClick={async () => {
                if (confirm(`Delete provider "${inst.name}"?`)) {
                  await deleteProvider(inst.id)
                  refresh()
                }
              }}
            >
              Delete
            </button>
          </div>
        ),
      )}
      {instances.length === 0 && !adding && (
        <div className="card muted">No providers configured yet.</div>
      )}
      {adding && (
        <ProviderForm
          types={types}
          onCancel={() => setAdding(false)}
          onSave={async (typeId, name, config) => {
            await createProvider(typeId, name, config)
            setAdding(false)
            refresh()
          }}
        />
      )}
    </section>
  )
}

function ProviderForm({
  types,
  initial,
  onSave,
  onCancel,
}: {
  types: ProviderType[]
  initial?: ProviderInstance
  onSave: (typeId: string, name: string, config: Record<string, string>) => Promise<void>
  onCancel: () => void
}) {
  const [typeId, setTypeId] = useState(initial?.type_id ?? types[0]?.type_id ?? '')
  const [name, setName] = useState(initial?.name ?? '')
  const [config, setConfig] = useState<Record<string, string>>(initial?.config ?? {})
  const [error, setError] = useState('')
  const type = types.find((t) => t.type_id === typeId)

  return (
    <div className="card form">
      {!initial && (
        <label>
          Type
          <select value={typeId} onChange={(e) => setTypeId(e.target.value)}>
            {types.map((t) => (
              <option key={t.type_id} value={t.type_id}>
                {t.display_name}
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        Name
        <input
          value={name}
          placeholder="e.g. Ollama local"
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      {type?.config_fields.map((f) => (
        <label key={f.key}>
          {f.label}
          {f.required ? ' *' : ''}
          <input
            type={f.type === 'password' ? 'password' : 'text'}
            value={config[f.key] ?? f.default}
            placeholder={f.placeholder}
            onChange={(e) => setConfig((c) => ({ ...c, [f.key]: e.target.value }))}
          />
        </label>
      ))}
      {error && <div className="form-error">⚠ {error}</div>}
      <div className="row">
        <button
          className="btn btn-primary"
          onClick={async () => {
            const missing = type?.config_fields.find((f) => f.required && !config[f.key])
            if (!name.trim()) return setError('Name is required')
            if (missing) return setError(`${missing.label} is required`)
            try {
              await onSave(typeId, name.trim(), config)
            } catch (e) {
              setError(String(e))
            }
          }}
        >
          Save
        </button>
        <button className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  )
}

function AgentAccessSection() {
  const [info, setInfo] = useState<AgentFolders | null>(null)
  const [newFolder, setNewFolder] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    getAgentFolders().then(setInfo).catch(console.error)
  }, [])

  async function save(folders: string[]) {
    try {
      setInfo(await setAgentFolders(folders))
      setError('')
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <section className="settings-section">
      <div className="section-head">
        <h2>Agent file access</h2>
      </div>
      <p className="hint">
        Folders the agent's file tools (<code>file_read</code>, <code>file_write</code>,{' '}
        <code>file_list</code>) may access with absolute paths, in addition to the built-in
        workspace. Shell commands are gated separately by per-call approval.
      </p>
      <div className="card row">
        <div className="grow">
          <strong>Workspace</strong>
          <div className="muted mono">{info?.workspace ?? '…'}</div>
        </div>
        <span className="muted">always on</span>
      </div>
      {info?.folders.map((f) => (
        <div key={f} className="card row">
          <div className="grow mono">
            {f}
            {info.missing.includes(f) && <span className="form-error"> (folder not found)</span>}
          </div>
          <button
            className="btn btn-danger"
            onClick={() => save(info.folders.filter((x) => x !== f))}
          >
            Remove
          </button>
        </div>
      ))}
      <div className="card row">
        <input
          className="mono grow"
          value={newFolder}
          placeholder="D:\some\folder"
          onChange={(e) => setNewFolder(e.target.value)}
        />
        <button
          className="btn btn-primary"
          disabled={!newFolder.trim() || !info}
          onClick={() => {
            save([...(info?.folders ?? []), newFolder.trim()])
            setNewFolder('')
          }}
        >
          Grant access
        </button>
      </div>
      {error && <div className="form-error">⚠ {error}</div>}
    </section>
  )
}

function MemorySection() {
  const [memories, setMemories] = useState<MemoryEntry[]>([])
  const [newMemory, setNewMemory] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const refresh = () => listMemories().then(setMemories).catch(console.error)
  useEffect(() => {
    refresh()
  }, [])

  async function run(action: () => Promise<unknown>): Promise<boolean> {
    setBusy(true)
    try {
      await action()
      setError('')
      refresh()
      return true
    } catch (e) {
      setError(String(e))
      return false
    } finally {
      setBusy(false)
    }
  }

  async function remember() {
    // clear only after the save succeeds - a 400 must not eat the typed text
    if (await run(() => addMemory(newMemory.trim()))) setNewMemory('')
  }

  return (
    <section className="settings-section">
      <div className="section-head">
        <h2>Agent memory</h2>
        {memories.length > 0 && (
          <button
            className="btn btn-danger"
            onClick={() => {
              if (confirm(`Forget all ${memories.length} memories?`)) run(clearMemories)
            }}
          >
            Forget all
          </button>
        )}
      </div>
      <p className="hint">
        Durable facts the agent saved with <code>memory_save</code> (or that you add here). They
        are shown to the agent at the start of every agent-mode conversation; normal chat never
        sees them. Stored locally in the database.
      </p>
      {memories.map((m) => (
        <div key={m.id} className="card row">
          <div className="grow">
            {m.content}
            <div className="muted">
              [{m.id}] · {new Date(m.created_at).toLocaleDateString()}
            </div>
          </div>
          <button
            className="btn btn-danger"
            disabled={busy}
            onClick={() => run(() => deleteMemory(m.id))}
          >
            Forget
          </button>
        </div>
      ))}
      {memories.length === 0 && (
        <div className="card muted">No memories yet - the agent saves them as you chat.</div>
      )}
      <div className="card row">
        <input
          className="grow"
          value={newMemory}
          maxLength={500}
          placeholder="Add a memory, e.g. I prefer answers in metric units"
          onChange={(e) => setNewMemory(e.target.value)}
          onKeyDown={(e) => {
            // isComposing: Enter that confirms an IME composition must not submit
            if (e.key === 'Enter' && !e.nativeEvent.isComposing && newMemory.trim() && !busy) {
              remember()
            }
          }}
        />
        <button
          className="btn btn-primary"
          disabled={!newMemory.trim() || busy}
          onClick={remember}
        >
          Remember
        </button>
      </div>
      {error && <div className="form-error">⚠ {error}</div>}
    </section>
  )
}

function McpSection() {
  const [servers, setServers] = useState<McpServer[]>([])
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [command, setCommand] = useState('')
  const [error, setError] = useState('')

  const refresh = () => listMcpServers().then(setServers).catch(console.error)
  useEffect(() => {
    refresh()
  }, [])

  return (
    <section className="settings-section">
      <div className="section-head">
        <h2>MCP servers</h2>
        <button className="btn btn-primary" onClick={() => setAdding(true)}>
          + Add server
        </button>
      </div>
      <p className="hint">
        Stdio MCP servers add tools to agent mode. Example command:{' '}
        <code>npx -y @modelcontextprotocol/server-filesystem D:\somewhere</code>
      </p>
      {servers.map((s) => (
        <div key={s.id} className="card row">
          <div className="grow">
            <strong>{s.name}</strong>
            <div className="muted mono">
              {s.command} {s.args.join(' ')}
            </div>
          </div>
          <label className="agent-toggle">
            <input
              type="checkbox"
              checked={Boolean(s.enabled)}
              onChange={async (e) => {
                await setMcpServerEnabled(s.id, e.target.checked)
                refresh()
              }}
            />
            <span>enabled</span>
          </label>
          <button
            className="btn btn-danger"
            onClick={async () => {
              if (confirm(`Delete MCP server "${s.name}"?`)) {
                await deleteMcpServer(s.id)
                refresh()
              }
            }}
          >
            Delete
          </button>
        </div>
      ))}
      {servers.length === 0 && !adding && (
        <div className="card muted">No MCP servers configured.</div>
      )}
      {adding && (
        <div className="card form">
          <label>
            Name
            <input value={name} placeholder="filesystem" onChange={(e) => setName(e.target.value)} />
          </label>
          <label>
            Command line
            <input
              value={command}
              className="mono"
              placeholder="npx -y @modelcontextprotocol/server-filesystem D:\data"
              onChange={(e) => setCommand(e.target.value)}
            />
          </label>
          {error && <div className="form-error">⚠ {error}</div>}
          <div className="row">
            <button
              className="btn btn-primary"
              onClick={async () => {
                const parts = command.trim().split(/\s+/)
                if (!name.trim() || parts.length === 0 || !parts[0]) {
                  return setError('Name and command are required')
                }
                try {
                  await createMcpServer(name.trim(), parts[0], parts.slice(1), {})
                  setAdding(false)
                  setName('')
                  setCommand('')
                  setError('')
                  refresh()
                } catch (e) {
                  setError(String(e))
                }
              }}
            >
              Save
            </button>
            <button className="btn" onClick={() => setAdding(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </section>
  )
}
