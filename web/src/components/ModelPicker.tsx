import { useEffect, useState } from 'react'
import { listProviderModels } from '../api'
import type { ModelInfo, ProviderInstance } from '../types'

interface Props {
  providers: ProviderInstance[]
  providerId: string
  model: string
  onProviderChange: (id: string) => void
  onModelChange: (model: string) => void
}

export default function ModelPicker({
  providers,
  providerId,
  model,
  onProviderChange,
  onModelChange,
}: Props) {
  const [models, setModels] = useState<ModelInfo[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    if (!providerId) {
      setModels([])
      return
    }
    let cancelled = false
    setError('')
    listProviderModels(providerId)
      .then((list) => {
        if (cancelled) return
        setModels(list)
        if (list.length && !list.some((m) => m.id === model)) {
          onModelChange(list[0].id)
        }
      })
      .catch((e) => {
        if (!cancelled) setError(String(e).slice(0, 120))
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providerId])

  return (
    <div className="model-picker">
      <select
        value={providerId}
        onChange={(e) => onProviderChange(e.target.value)}
        title="Provider instance"
      >
        {providers.length === 0 && <option value="">No providers - add one in Settings</option>}
        {providers.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
      <select value={model} onChange={(e) => onModelChange(e.target.value)} title="Model">
        {models.length === 0 && <option value="">{error ? 'unavailable' : 'no models'}</option>}
        {models.map((m) => (
          <option key={m.id} value={m.id}>
            {m.id}
          </option>
        ))}
      </select>
      {error && (
        <span className="picker-error" title={error}>
          ⚠
        </span>
      )}
    </div>
  )
}
