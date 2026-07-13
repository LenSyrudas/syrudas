import { useEffect, useRef, useState } from 'react'
import {
  getArenaLeaderboard,
  listProviderModels,
  recordArenaVote,
  resetArenaLeaderboard,
  streamComplete,
} from '../api'
import type { ArenaStanding, ArenaWinner } from '../api'
import type { ProviderInstance } from '../types'
import Markdown from './Markdown'

interface Pick {
  providerId: string
  model: string
}

interface Side {
  pick: Pick
  text: string
  streaming: boolean
  error: string
}

// Fisher-Yates isn't needed for two items: a coin flip decides which chosen
// model is shown on the left, so column position never leaks the identity.
function coinFlip(): boolean {
  return Math.random() < 0.5
}

function ModelSelect({
  providers,
  pick,
  onChange,
  label,
}: {
  providers: ProviderInstance[]
  pick: Pick
  onChange: (p: Pick) => void
  label: string
}) {
  const [models, setModels] = useState<string[]>([])
  useEffect(() => {
    if (!pick.providerId) return
    listProviderModels(pick.providerId)
      .then((list) => {
        const ids = list.map((m) => m.id)
        setModels(ids)
        // reconcile the selection: a model from the previous provider won't
        // exist on the new one, so snap to a valid option (mirrors ModelPicker)
        if (ids.length && !ids.includes(pick.model)) onChange({ ...pick, model: ids[0] })
        else if (!ids.length && pick.model) onChange({ ...pick, model: '' })
      })
      .catch(() => setModels([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pick.providerId])

  return (
    <div className="arena-pick">
      <span className="muted">{label}</span>
      <select
        value={pick.providerId}
        onChange={(e) => onChange({ ...pick, providerId: e.target.value })}
      >
        {providers.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
      <select value={pick.model} onChange={(e) => onChange({ ...pick, model: e.target.value })}>
        {models.length === 0 && <option value="">{pick.model || 'no models'}</option>}
        {models.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
    </div>
  )
}

export default function ArenaView({ providers }: { providers: ProviderInstance[] }) {
  const first = providers[0]?.id ?? ''
  const [prompt, setPrompt] = useState('')
  const [pickA, setPickA] = useState<Pick>({ providerId: first, model: '' })
  const [pickB, setPickB] = useState<Pick>({ providerId: first, model: '' })
  const [left, setLeft] = useState<Side | null>(null)
  const [right, setRight] = useState<Side | null>(null)
  const [running, setRunning] = useState(false)
  const [revealed, setRevealed] = useState(false)
  const [voted, setVoted] = useState<ArenaWinner | null>(null)
  const [voteError, setVoteError] = useState('')
  const [board, setBoard] = useState<ArenaStanding[]>([])
  const abortRef = useRef<AbortController[]>([])

  const refreshBoard = () => getArenaLeaderboard().then(setBoard).catch(console.error)
  useEffect(() => {
    refreshBoard()
  }, [])

  // seed default models once the first provider's list resolves
  useEffect(() => {
    if (!first) return
    listProviderModels(first)
      .then((list) => {
        const ids = list.map((m) => m.id)
        if (ids.length) {
          setPickA((p) => (p.model ? p : { providerId: first, model: ids[0] }))
          // leave B empty (not a duplicate of A) when only one model exists,
          // so Compare stays disabled rather than seeding a self-match
          setPickB((p) => (p.model ? p : { providerId: first, model: ids[1] ?? '' }))
        }
      })
      .catch(() => {})
  }, [first])

  const sameModel = Boolean(pickA.model) && pickA.model === pickB.model
  const canRun =
    Boolean(prompt.trim() && pickA.providerId && pickA.model && pickB.providerId && pickB.model) &&
    !sameModel &&
    !running

  async function runOne(pick: Pick, set: (fn: (s: Side) => Side) => void, signal: AbortSignal) {
    try {
      await streamComplete(
        { provider_id: pick.providerId, model: pick.model, message: prompt.trim() },
        (ev) => {
          if (ev.type === 'text_delta' && ev.text) {
            const delta = ev.text
            set((s) => ({ ...s, text: s.text + delta }))
          } else if (ev.type === 'error') {
            const msg = ev.message ?? 'error'
            set((s) => ({ ...s, error: msg }))
          }
        },
        signal,
      )
    } catch (e) {
      if ((e as Error).name !== 'AbortError') set((s) => ({ ...s, error: String(e) }))
    } finally {
      set((s) => ({ ...s, streaming: false }))
    }
  }

  async function compare() {
    if (!canRun) return
    // coin-flip which chosen model lands in the left column (blind)
    const [lp, rp] = coinFlip() ? [pickA, pickB] : [pickB, pickA]
    const blank = (pick: Pick): Side => ({ pick, text: '', streaming: true, error: '' })
    setLeft(blank(lp))
    setRight(blank(rp))
    setRevealed(false)
    setVoted(null)
    setVoteError('')
    setRunning(true)
    const ctrls = [new AbortController(), new AbortController()]
    abortRef.current = ctrls
    await Promise.all([
      runOne(lp, (fn) => setLeft((s) => (s ? fn(s) : s)), ctrls[0].signal),
      runOne(rp, (fn) => setRight((s) => (s ? fn(s) : s)), ctrls[1].signal),
    ])
    setRunning(false)
  }

  function stop() {
    abortRef.current.forEach((c) => c.abort())
  }

  async function vote(winner: ArenaWinner) {
    if (!left || !right || voted) return
    // record first: only claim "Recorded" and reveal once the write succeeds,
    // so a failed POST doesn't tell the user a dropped vote was saved
    try {
      await recordArenaVote(labelOf(left.pick), labelOf(right.pick), winner)
      setVoted(winner)
      setRevealed(true)
      setVoteError('')
      refreshBoard()
    } catch (e) {
      setVoteError(`Could not record vote: ${e}`)
    }
  }

  const bothDone = Boolean(left && right && !left.streaming && !right.streaming)

  return (
    <div className="arena">
      <div className="arena-head">
        <h1>Blind arena</h1>
        <p className="hint">
          Pit two models against the same prompt with their names hidden, vote for the better
          answer, then reveal. Votes build a local leaderboard.
        </p>
        <div className="arena-picks">
          <ModelSelect providers={providers} pick={pickA} onChange={setPickA} label="Model 1" />
          <ModelSelect providers={providers} pick={pickB} onChange={setPickB} label="Model 2" />
        </div>
        <textarea
          className="arena-prompt"
          value={prompt}
          placeholder="Ask both models the same thing…"
          rows={2}
          onChange={(e) => setPrompt(e.target.value)}
        />
        <div className="row">
          {running ? (
            <button className="btn btn-danger" onClick={stop}>
              Stop
            </button>
          ) : (
            <button className="btn btn-primary" disabled={!canRun} onClick={compare}>
              Compare
            </button>
          )}
          {sameModel && <span className="muted">Pick two different models.</span>}
        </div>
      </div>

      {left && right && (
        <div className="arena-grid">
          {[
            { side: left, key: 'a' as const, name: 'Model A' },
            { side: right, key: 'b' as const, name: 'Model B' },
          ].map(({ side, key, name }) => (
            <div key={key} className="arena-col">
              <div className="arena-col-head">
                {revealed ? <strong>{labelOf(side.pick)}</strong> : <strong>{name}</strong>}
                {side.streaming && <span className="muted"> · generating…</span>}
              </div>
              <div className="arena-answer">
                {side.error ? (
                  // the raw provider error can name the model - keep it hidden
                  // until the vote so it doesn't break the blind
                  <div className="form-error">
                    ⚠ {revealed ? side.error : 'This model returned an error.'}
                  </div>
                ) : (
                  <Markdown>{side.text || (side.streaming ? '' : '(no output)')}</Markdown>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {voteError && <div className="form-error" style={{ textAlign: 'center' }}>⚠ {voteError}</div>}
      {bothDone && !voted && (
        <div className="arena-vote">
          <span className="muted">Which is better?</span>
          <button className="btn" onClick={() => vote('a')}>
            ◀ Model A
          </button>
          <button className="btn" onClick={() => vote('b')}>
            Model B ▶
          </button>
          <button className="btn" onClick={() => vote('tie')}>
            Tie
          </button>
          <button className="btn" onClick={() => vote('both_bad')}>
            Both bad
          </button>
        </div>
      )}
      {voted && (
        <div className="arena-result muted">
          Recorded:{' '}
          {voted === 'tie'
            ? 'tie'
            : voted === 'both_bad'
              ? 'both bad'
              : `winner ${labelOf((voted === 'a' ? left : right)!.pick)}`}
        </div>
      )}

      {board.length > 0 && (
        <div className="arena-board">
          <div className="section-head">
            <h2>Leaderboard</h2>
            <button
              className="btn btn-danger"
              onClick={() => {
                if (confirm('Reset the leaderboard?')) resetArenaLeaderboard().then(refreshBoard)
              }}
            >
              Reset
            </button>
          </div>
          <table className="arena-table">
            <thead>
              <tr>
                <th>Model</th>
                <th>Games</th>
                <th>Wins</th>
                <th>Losses</th>
                <th>Ties</th>
                <th>Win rate</th>
              </tr>
            </thead>
            <tbody>
              {board.map((s) => (
                <tr key={s.model}>
                  <td className="mono">{s.model}</td>
                  <td>{s.games}</td>
                  <td>{s.wins}</td>
                  <td>{s.losses}</td>
                  <td>{s.ties}</td>
                  <td>{(s.win_rate * 100).toFixed(0)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function labelOf(pick: Pick): string {
  return pick.model
}
