import { useCallback, useEffect, useState } from 'react'
import { listConversations, listProviders } from './api'
import ChatView from './components/ChatView'
import ModelPicker from './components/ModelPicker'
import SettingsView from './components/SettingsView'
import Sidebar from './components/Sidebar'
import type { Conversation, ProviderInstance } from './types'

function App() {
  const [view, setView] = useState<'chat' | 'settings'>('chat')
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  // Remount ChatView only when the user switches chats — NOT when a new
  // conversation gets its id mid-stream, or the live stream display is lost.
  const [chatKey, setChatKey] = useState('new')
  const [providers, setProviders] = useState<ProviderInstance[]>([])
  const [providerId, setProviderId] = useState<string>(
    () => localStorage.getItem('argos.providerId') ?? '',
  )
  const [model, setModel] = useState<string>(() => localStorage.getItem('argos.model') ?? '')
  const [agentMode, setAgentMode] = useState(
    () => localStorage.getItem('argos.agentMode') === '1',
  )

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
    localStorage.setItem('argos.providerId', providerId)
  }, [providerId])
  useEffect(() => {
    localStorage.setItem('argos.model', model)
  }, [model])
  useEffect(() => {
    localStorage.setItem('argos.agentMode', agentMode ? '1' : '0')
  }, [agentMode])

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
          setView('chat')
        }}
        onDeleted={(id) => {
          if (id === activeId) setActiveId(null)
          refreshConversations()
        }}
        onSettings={() => setView('settings')}
        settingsActive={view === 'settings'}
      />
      <main className="main">
        {view === 'settings' ? (
          <SettingsView onProvidersChanged={refreshProviders} />
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
            </header>
            <ChatView
              key={chatKey}
              conversationId={activeId}
              providerId={providerId}
              model={model}
              agentMode={agentMode}
              onConversationCreated={(id) => {
                setActiveId(id)
                refreshConversations()
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
