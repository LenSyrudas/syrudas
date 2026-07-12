import { useEffect, useRef, useState } from 'react'
import { getPromptPresets, setPromptPresets } from '../api'
import type { GenParams, PromptPreset } from '../api'

/** Temperature / max-tokens popover for the topbar. */
export function TuningPopover({
  params,
  onChange,
}: {
  params: GenParams
  onChange: (p: GenParams) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])

  const active = params.temperature !== undefined || params.max_tokens !== undefined

  return (
    <div className="popover-anchor" ref={ref}>
      <button
        className={`btn btn-compact ${active ? 'active-control' : ''}`}
        title="Generation settings (temperature, max tokens)"
        onClick={() => setOpen(!open)}
      >
        🎛{active ? '•' : ''}
      </button>
      {open && (
        <div className="popover">
          <label className="popover-row">
            <span>
              Temperature{' '}
              <span className="muted">
                {params.temperature !== undefined ? params.temperature.toFixed(1) : 'default'}
              </span>
            </span>
            <input
              type="range"
              min={0}
              max={2}
              step={0.1}
              value={params.temperature ?? 0.8}
              onChange={(e) => onChange({ ...params, temperature: Number(e.target.value) })}
            />
          </label>
          <label className="popover-row">
            <span>Max tokens <span className="muted">{params.max_tokens ?? 'default'}</span></span>
            <input
              type="number"
              min={1}
              step={1}
              placeholder="default"
              value={params.max_tokens ?? ''}
              onChange={(e) => {
                // backend expects an integer: floor and reject NaN, or a
                // fractional value would 422 every send until reset
                const n = Math.floor(Number(e.target.value))
                onChange({
                  ...params,
                  max_tokens: e.target.value && Number.isFinite(n) ? Math.max(1, n) : undefined,
                })
              }}
            />
          </label>
          <button
            className="btn btn-compact"
            onClick={() => onChange({})}
            disabled={!active}
          >
            Reset to defaults
          </button>
        </div>
      )}
    </div>
  )
}

/** System-prompt editor with saved presets, rendered under the topbar. */
export function PersonaPanel({
  systemPrompt,
  onChange,
  onClose,
}: {
  systemPrompt: string
  onChange: (prompt: string) => void
  onClose: () => void
}) {
  const [presets, setPresets] = useState<PromptPreset[]>([])
  const [presetName, setPresetName] = useState('')

  useEffect(() => {
    getPromptPresets().then((r) => setPresets(r.presets)).catch(console.error)
  }, [])

  async function savePreset() {
    const name = presetName.trim()
    if (!name || !systemPrompt.trim()) return
    const next = [...presets.filter((p) => p.name !== name), { name, prompt: systemPrompt }]
    const saved = await setPromptPresets(next)
    setPresets(saved.presets)
    setPresetName('')
  }

  async function deletePreset(name: string) {
    const saved = await setPromptPresets(presets.filter((p) => p.name !== name))
    setPresets(saved.presets)
  }

  return (
    <div className="persona-panel">
      <div className="persona-head">
        <strong>System prompt</strong>
        <span className="muted">
          Sets the assistant's role for this conversation. Applies from the next message.
        </span>
        <button className="icon-btn" title="Close" onClick={onClose}>
          ✕
        </button>
      </div>
      <textarea
        value={systemPrompt}
        placeholder="e.g. You are a senior Python reviewer. Be terse and concrete."
        rows={3}
        onChange={(e) => onChange(e.target.value)}
      />
      <div className="persona-presets">
        {presets.map((p) => (
          <span key={p.name} className="pending-chip">
            <button className="preset-apply" title={p.prompt} onClick={() => onChange(p.prompt)}>
              {p.name}
            </button>
            <button className="icon-btn" title="Delete preset" onClick={() => deletePreset(p.name)}>
              ✕
            </button>
          </span>
        ))}
        <input
          value={presetName}
          placeholder="save as preset…"
          onChange={(e) => setPresetName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) savePreset()
          }}
        />
        <button
          className="btn btn-compact"
          disabled={!presetName.trim() || !systemPrompt.trim()}
          onClick={savePreset}
        >
          Save
        </button>
      </div>
    </div>
  )
}
