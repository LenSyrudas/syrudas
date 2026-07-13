import { useEffect, useMemo, useRef, useState } from 'react'
import { deleteModel, getCookbook, streamPullModel } from '../api'
import type { CatalogModel, Cookbook } from '../api'

const FIT_LABEL: Record<CatalogModel['fit'], { text: string; cls: string }> = {
  good: { text: 'Fits your GPU', cls: 'fit-good' },
  tight: { text: 'Tight fit', cls: 'fit-tight' },
  cpu: { text: 'CPU / offload', cls: 'fit-cpu' },
  too_big: { text: 'Too big', cls: 'fit-big' },
  unknown: { text: 'Unknown', cls: 'fit-unknown' },
}

const FILTERS = ['all', 'chat', 'tools', 'code', 'reasoning', 'vision', 'embedding']

function gb(mb: number | null): string {
  return mb ? `${(mb / 1024).toFixed(mb / 1024 >= 10 ? 0 : 1)} GB` : '—'
}

export default function CookbookView() {
  const [data, setData] = useState<Cookbook | null>(null)
  const [loadError, setLoadError] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({}) // per-model action errors
  const [filter, setFilter] = useState('all')
  // per-model pull progress: absent = not pulling
  const [progress, setProgress] = useState<Record<string, { status: string; percent: number | null }>>({})
  const aborts = useRef<Record<string, AbortController>>({})

  const refresh = () => getCookbook().then(setData).catch((e) => setLoadError(String(e)))
  useEffect(() => {
    refresh()
  }, [])

  const ollamaReady = Boolean(data?.ollama.configured)

  const shown = useMemo(
    () => (data?.catalog ?? []).filter((m) => filter === 'all' || m.tags.includes(filter)),
    [data, filter],
  )

  function setModelError(name: string, msg: string) {
    setErrors((e) => ({ ...e, [name]: msg }))
  }
  function clearModelError(name: string) {
    setErrors((e) => {
      if (!(name in e)) return e
      const next = { ...e }
      delete next[name]
      return next
    })
  }

  async function pull(name: string) {
    clearModelError(name)
    setProgress((p) => ({ ...p, [name]: { status: 'starting…', percent: null } }))
    const controller = new AbortController()
    aborts.current[name] = controller
    try {
      await streamPullModel(
        name,
        (ev) => {
          if (ev.type === 'progress') {
            setProgress((p) => ({ ...p, [name]: { status: ev.status ?? '', percent: ev.percent ?? null } }))
          } else if (ev.type === 'error') {
            setModelError(name, ev.message ?? 'pull failed')
          }
        },
        controller.signal,
      )
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setModelError(name, String(e))
    } finally {
      delete aborts.current[name]
      // load the new installed state BEFORE dropping the progress row, so the
      // card doesn't flip back to a Download button mid-refetch (double-pull)
      await refresh()
      setProgress((p) => {
        const next = { ...p }
        delete next[name]
        return next
      })
    }
  }

  async function remove(name: string) {
    clearModelError(name)
    try {
      await deleteModel(name)
      await refresh()
    } catch (e) {
      setModelError(name, String(e))
    }
  }

  return (
    <div className="cookbook">
      <div className="cookbook-head">
        <h1>Model cookbook</h1>
        <p className="hint">
          Hardware-aware model picks you can download into Ollama with one click. Pulled models
          appear in the normal model picker. Fit ratings are estimates — real use depends on
          quantization and context length.
        </p>
      </div>

      {data && (
        <div className="hw-card">
          <div>
            <span className="muted">CPU</span>
            <div>{data.hardware.cpu.name}</div>
            <div className="muted">
              {data.hardware.cpu.cores ?? '?'} cores / {data.hardware.cpu.threads ?? '?'} threads
            </div>
          </div>
          <div>
            <span className="muted">Memory</span>
            <div>{gb(data.hardware.ram.total_mb)} RAM</div>
          </div>
          <div>
            <span className="muted">GPU</span>
            {data.hardware.gpus.length === 0 ? (
              <div className="muted">none detected</div>
            ) : (
              data.hardware.gpus.map((g, i) => (
                <div key={i}>
                  {g.name}
                  <span className="muted">
                    {' '}
                    — {gb(g.vram_total_mb)} VRAM{g.vram_estimated ? ' (est.)' : ''}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {data && !ollamaReady && (
        <div className="cookbook-banner">
          ⚠ No Ollama detected at <code>localhost:11434</code>. Install and run{' '}
          <a href="https://ollama.com" target="_blank" rel="noreferrer">
            Ollama
          </a>{' '}
          to download models. The recommendations below still apply.
        </div>
      )}
      {loadError && <div className="form-error">⚠ {loadError}</div>}

      <div className="cookbook-filters">
        {FILTERS.map((f) => (
          <button
            key={f}
            className={`btn btn-compact ${filter === f ? 'active-control' : ''}`}
            onClick={() => setFilter(f)}
          >
            {f}
          </button>
        ))}
      </div>

      <div className="model-grid">
        {shown.map((m) => {
          const fit = FIT_LABEL[m.fit]
          const prog = progress[m.name]
          const modelError = errors[m.name]
          return (
            <div key={m.name} className="model-card">
              <div className="model-card-head">
                <strong className="mono">{m.name}</strong>
                <span className={`fit-badge ${fit.cls}`}>{fit.text}</span>
              </div>
              <div className="muted">
                {m.params} · {m.size_gb} GB download
              </div>
              <div className="model-tags">
                {m.tags.map((t) => (
                  <span key={t} className="model-tag">
                    {t}
                  </span>
                ))}
              </div>
              <div className="model-blurb">{m.blurb}</div>
              <div className="muted model-fit-reason">{m.fit_reason}</div>
              <div className="model-card-foot">
                {m.installed ? (
                  <>
                    <span className="fit-badge fit-good">✓ Installed</span>
                    <button
                      className="btn btn-compact btn-danger"
                      disabled={!ollamaReady}
                      onClick={() => {
                        if (confirm(`Remove ${m.name} from Ollama?`)) remove(m.name)
                      }}
                    >
                      Remove
                    </button>
                  </>
                ) : prog ? (
                  <div className="pull-progress">
                    <div className="pull-bar">
                      <div
                        className="pull-bar-fill"
                        style={{ width: `${prog.percent ?? 5}%` }}
                      />
                    </div>
                    <span className="muted">
                      {prog.status}
                      {prog.percent != null ? ` ${prog.percent}%` : ''}
                    </span>
                    <button
                      className="btn btn-compact"
                      onClick={() => aborts.current[m.name]?.abort()}
                    >
                      Stop
                    </button>
                  </div>
                ) : (
                  <button
                    className="btn btn-compact btn-primary"
                    disabled={!ollamaReady}
                    title={ollamaReady ? 'Download into Ollama' : 'Requires Ollama'}
                    onClick={() => pull(m.name)}
                  >
                    ⬇ Download
                  </button>
                )}
              </div>
              {modelError && <div className="form-error model-fit-reason">⚠ {modelError}</div>}
            </div>
          )
        })}
      </div>
    </div>
  )
}
