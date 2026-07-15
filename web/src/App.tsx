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

function App() {
  const [view, setView] = useState<'chat' | 'settings' | 'arena' | 'editor' | 'cookbook'>('chat')
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  // Remount ChatView only when the user switches chats — NOT when a new
  // conversation gets its id mid-stream, or the live stream display is lost.
  const [chatKey, setChatKey] = useState('new')
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

  const refreshConversations = useCallback(() => {
    listConversations().then(setConversations).catch(console.error)
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
    localStorage.setItem('syrudas.providerId', providerId)
  }, [providerId])
  useEffect(() => {
    localStorage.setItem('syrudas.model', model)
  }, [model])
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
              onConversationLoaded={(conv) => setSystemPrompt(conv.system_prompt ?? '')}
              onStreamEnd={refreshConversations}
            />
          </>
        )}
      </main>
    </div>
  )
}

export default App
