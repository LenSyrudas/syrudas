import { useCallback, useEffect, useRef, useState } from 'react'
import { exportConversationUrl, listConversations, listProviders, patchConversation } from './api'
import type { GenParams } from './api'
import ArenaView from './components/ArenaView'
import { PersonaPanel, TuningPopover } from './components/ChatControls'
import ChatView from './components/ChatView'
import CookbookView from './components/CookbookView'
import EditorView from './components/EditorView'
import ModelPicker from './components/ModelPicker'
import SettingsView from './components/SettingsView'
import Sidebar from './components/Sidebar'
import type { Conversation, ProviderInstance } from './types'

function loadGenParams(): GenParams {
  try {
    return JSON.parse(localStorage.getItem('syrudas.genParams') ?? '{}') as GenParams
  } catch {
    return {}
  }
}

type View = 'chat' | 'settings' | 'arena' | 'editor' | 'cookbook'
const VIEWS: View[] = ['chat', 'settings', 'arena', 'editor', 'cookbook']

function App() {
  // restore where the user left off: the last view and open conversation
  const [view, setView] = useState<View>(() => {
    const v = localStorage.getItem('syrudas.view') as View | null
    return v && VIEWS.includes(v) ? v : 'chat'
  })
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [convsLoaded, setConvsLoaded] = useState(false)
  const [activeId, setActiveId] = useState<string | null>(
    () => localStorage.getItem('syrudas.activeId') || null,
  )
  // Remount ChatView only when the user switches chats — NOT when a new
  // conversation gets its id mid-stream, or the live stream display is lost.
  const [chatKey, setChatKey] = useState(() => localStorage.getItem('syrudas.activeId') || 'new')
  const [providers, setProviders] = useState<ProviderInstance[]>([])
  const [providerId, setProviderId] = useState<string>(
    () => localStorage.getItem('syrudas.providerId') ?? '',
  )
  const [model, setModel] = useState<string>(() => localStorage.getItem('syrudas.model') ?? '')
  const [agentMode, setAgentMode] = useState(
    () => localStorage.getItem('syrudas.agentMode') === '1',
  )
  const [genParams, setGenParams] = useState<GenParams>(loadGenParams)
  const [systemPrompt, setSystemPrompt] = useState('')
  const [personaOpen, setPersonaOpen] = useState(false)
  // The just-opened conversation's stored provider/model, applied to the picker
  // once providers have loaded (see the reconcile effect below). Decoupled from
  // the load callback so a cold-reload restore isn't lost to an empty providers
  // list, and set only on a genuine open — not editLast/regenerate resyncs — so
  // a manual mid-conversation model switch stands.
  const [restoreTarget, setRestoreTarget] = useState<{
    provider_id: string | null
    model: string | null
  } | null>(null)

  const refreshConversations = useCallback(() => {
    listConversations()
      .then((list) => {
        setConversations(list)
        setConvsLoaded(true)
      })
      .catch(console.error)
  }, [])

  const refreshProviders = useCallback(() => {
    listProviders()
      .then((list) => {
        setProviders(list)
        setProviderId((current) =>
          list.some((p) => p.id === current) ? current : (list[0]?.id ?? ''),
        )
      })
      .catch(console.error)
  }, [])

  useEffect(() => {
    refreshConversations()
    refreshProviders()
  }, [refreshConversations, refreshProviders])

  useEffect(() => {
    localStorage.setItem('syrudas.view', view)
  }, [view])
  useEffect(() => {
    if (activeId) localStorage.setItem('syrudas.activeId', activeId)
    else localStorage.removeItem('syrudas.activeId')
  }, [activeId])
  // once conversations have loaded, drop a restored activeId whose conversation
  // was deleted in a previous session (otherwise ChatView 404s on load)
  const restoreChecked = useRef(false)
  useEffect(() => {
    if (!convsLoaded || restoreChecked.current) return
    restoreChecked.current = true
    if (activeId && !conversations.some((c) => c.id === activeId)) {
      setActiveId(null)
      setChatKey(`new-${Date.now()}`)
    }
  }, [convsLoaded, conversations, activeId])

  useEffect(() => {
    localStorage.setItem('syrudas.providerId', providerId)
  }, [providerId])
  useEffect(() => {
    localStorage.setItem('syrudas.model', model)
  }, [model])
  // apply a pending conversation model-restore once providers are available;
  // re-runs when providers arrive (cold reload) and consumes the target once.
  // Skips silently if the conversation's provider no longer exists.
  useEffect(() => {
    if (!restoreTarget || providers.length === 0) return
    const { provider_id, model: storedModel } = restoreTarget
    setRestoreTarget(null)
    if (provider_id && providers.some((p) => p.id === provider_id)) {
      setProviderId(provider_id)
      if (storedModel) setModel(storedModel)
    }
  }, [restoreTarget, providers])
  useEffect(() => {
    localStorage.setItem('syrudas.agentMode', agentMode ? '1' : '0')
  }, [agentMode])
  useEffect(() => {
    localStorage.setItem('syrudas.genParams', JSON.stringify(genParams))
  }, [genParams])

  const patchTimer = useRef<number | null>(null)
  function changeSystemPrompt(prompt: string) {
    setSystemPrompt(prompt)
    // debounce the PATCH: this fires per keystroke, and each write also bumps
    // the conversation's updated_at (sidebar order)
    if (!activeId) return // new chat: the prompt travels with the first message
    const id = activeId
    if (patchTimer.current) window.clearTimeout(patchTimer.current)
    patchTimer.current = window.setTimeout(() => {
      patchConversation(id, { system_prompt: prompt }).catch(console.error)
    }, 600)
  }

  return (
    <div className="app">
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        onSelect={(id) => {
          setActiveId(id)
          setChatKey(id)
          setView('chat')
        }}
        onNew={() => {
          setActiveId(null)
          setChatKey(`new-${Date.now()}`)
          setSystemPrompt('')
          setView('chat')
        }}
        onDeleted={(id) => {
          if (id === activeId) setActiveId(null)
          refreshConversations()
        }}
        onRenamed={refreshConversations}
        onSettings={() => setView('settings')}
        onArena={() => setView('arena')}
        onEditor={() => setView('editor')}
        onCookbook={() => setView('cookbook')}
        settingsActive={view === 'settings'}
        arenaActive={view === 'arena'}
        editorActive={view === 'editor'}
        cookbookActive={view === 'cookbook'}
      />
      <main className="main">
        {view === 'settings' ? (
          <SettingsView onProvidersChanged={refreshProviders} />
        ) : view === 'arena' ? (
          <ArenaView providers={providers} />
        ) : view === 'editor' ? (
          <EditorView providerId={providerId} model={model} />
        ) : view === 'cookbook' ? (
          <CookbookView />
        ) : (
          <>
            <header className="topbar">
              <ModelPicker
                providers={providers}
                providerId={providerId}
                model={model}
                onProviderChange={setProviderId}
                onModelChange={setModel}
              />
              <label className="agent-toggle" title="Let the model plan and call tools">
                <input
                  type="checkbox"
                  checked={agentMode}
                  onChange={(e) => setAgentMode(e.target.checked)}
                />
                <span>Agent mode</span>
              </label>
              <div className="topbar-spacer" />
              <TuningPopover params={genParams} onChange={setGenParams} />
              <button
                className={`btn btn-compact ${systemPrompt ? 'active-control' : ''}`}
                title="System prompt / persona"
                aria-label="System prompt / persona"
                aria-pressed={personaOpen}
                onClick={() => setPersonaOpen(!personaOpen)}
              >
                🎭{systemPrompt ? '•' : ''}
              </button>
              {activeId && (
                <a
                  className="btn btn-compact"
                  title="Export conversation as Markdown"
                  aria-label="Export conversation as Markdown"
                  href={exportConversationUrl(activeId)}
                  download
                >
                  ⤓
                </a>
              )}
            </header>
            {personaOpen && (
              <PersonaPanel
                systemPrompt={systemPrompt}
                onChange={changeSystemPrompt}
                onClose={() => setPersonaOpen(false)}
              />
            )}
            <ChatView
              key={chatKey}
              conversationId={activeId}
              providerId={providerId}
              model={model}
              agentMode={agentMode}
              genParams={genParams}
              systemPrompt={systemPrompt}
              onConversationCreated={(id) => {
                setActiveId(id)
                refreshConversations()
              }}
              onConversationLoaded={(conv, initial) => {
                setSystemPrompt(conv.system_prompt ?? '')
                // On a genuine open, queue the conversation's model/provider for
                // restore so a reply can't silently come from a different model
                // than the rest of the thread. The reconcile effect applies it
                // once providers load. Skip on editLast/regenerate resyncs so a
                // manual model switch before editing isn't reverted.
                if (initial) {
                  setRestoreTarget({ provider_id: conv.provider_id, model: conv.model })
                }
              }}
              onStreamEnd={refreshConversations}
            />
          </>
        )}
      </main>
    </div>
  )
}

export default App
