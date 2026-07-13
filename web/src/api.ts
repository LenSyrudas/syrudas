import type {
  Conversation,
  McpServer,
  ModelInfo,
  ProviderInstance,
  ProviderType,
  StreamEvent,
} from './types'

async function json<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const body = await resp.text()
    throw new Error(errorMessage(resp.status, body))
  }
  return resp.json() as Promise<T>
}

function errorMessage(status: number, body: string): string {
  try {
    const detail = (JSON.parse(body) as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
  } catch {
    /* not JSON */
  }
  return `${status}: ${body.slice(0, 300)}`
}

const jsonHeaders = { 'Content-Type': 'application/json' }

// --- attachments ---

export interface Attachment {
  name: string
  content: string
  chars: number
  truncated: boolean
}

export function uploadAttachment(file: File): Promise<Attachment> {
  const form = new FormData()
  form.append('file', file)
  return fetch('/api/attachments', { method: 'POST', body: form }).then((r) =>
    json<Attachment>(r),
  )
}

// --- settings ---

export interface AgentFolders {
  workspace: string
  folders: string[]
  missing: string[]
}

export const getAgentFolders = () =>
  fetch('/api/settings/agent-folders').then((r) => json<AgentFolders>(r))

export const setAgentFolders = (folders: string[]) =>
  fetch('/api/settings/agent-folders', {
    method: 'PUT',
    headers: jsonHeaders,
    body: JSON.stringify({ folders }),
  }).then((r) => json<AgentFolders>(r))

// --- conversations ---

export const listConversations = () =>
  fetch('/api/conversations').then((r) => json<Conversation[]>(r))

export const getConversation = (id: string) =>
  fetch(`/api/conversations/${id}`).then((r) => json<Conversation>(r))

export const deleteConversation = (id: string) =>
  fetch(`/api/conversations/${id}`, { method: 'DELETE' }).then((r) => json<{ ok: boolean }>(r))

export const patchConversation = (id: string, patch: Partial<Conversation>) =>
  fetch(`/api/conversations/${id}`, {
    method: 'PATCH',
    headers: jsonHeaders,
    body: JSON.stringify(patch),
  }).then((r) => json<Conversation>(r))

export const rewindConversation = (id: string, includeLastUser: boolean) =>
  fetch(`/api/conversations/${id}/rewind`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ include_last_user: includeLastUser }),
  }).then((r) => json<{ ok: boolean; removed_user_content: string | null }>(r))

export const exportConversationUrl = (id: string) => `/api/conversations/${id}/export`

// --- agent memory ---

export interface MemoryEntry {
  id: string
  content: string
  created_at: string
  updated_at: string
}

export const listMemories = () => fetch('/api/memories').then((r) => json<MemoryEntry[]>(r))

export const addMemory = (content: string) =>
  fetch('/api/memories', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ content }),
  }).then((r) => json<MemoryEntry>(r))

export const deleteMemory = (id: string) =>
  fetch(`/api/memories/${id}`, { method: 'DELETE' }).then((r) => json<{ ok: boolean }>(r))

export const clearMemories = () =>
  fetch('/api/memories', { method: 'DELETE' }).then((r) => json<{ deleted: number }>(r))

// --- knowledge (local RAG) ---

export interface KnowledgeSource {
  id: string
  path: string
  kind: string
  chars: number
  chunk_count: number
  indexed_at: string
}

export interface KnowledgeInfo {
  embedding: { provider_id: string; model: string } | null
  sources: KnowledgeSource[]
  chunks: number
}

export interface KnowledgeIndexResult {
  indexed: { path: string; chunks: number }[]
  skipped: string[]
}

export interface KnowledgeHit {
  path: string
  seq: number
  score: number
  content: string
}

export const getKnowledge = () => fetch('/api/knowledge').then((r) => json<KnowledgeInfo>(r))

export const setKnowledgeEmbedding = (providerId: string, model: string) =>
  fetch('/api/knowledge/embedding', {
    method: 'PUT',
    headers: jsonHeaders,
    body: JSON.stringify({ provider_id: providerId, model }),
  }).then((r) => json<{ ok: boolean; dim: number; cleared_sources: number }>(r))

export const indexKnowledgePath = (path: string) =>
  fetch('/api/knowledge/index', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ path }),
  }).then((r) => json<KnowledgeIndexResult>(r))

export const searchKnowledge = (query: string) =>
  fetch('/api/knowledge/search', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ query }),
  }).then((r) => json<{ results: KnowledgeHit[] }>(r))

export const deleteKnowledgeSource = (id: string) =>
  fetch(`/api/knowledge/sources/${id}`, { method: 'DELETE' }).then((r) =>
    json<{ ok: boolean }>(r),
  )

export const clearKnowledge = () =>
  fetch('/api/knowledge', { method: 'DELETE' }).then((r) => json<{ deleted: number }>(r))

// --- prompt presets ---

export interface PromptPreset {
  name: string
  prompt: string
}

export const getPromptPresets = () =>
  fetch('/api/settings/prompt-presets').then((r) => json<{ presets: PromptPreset[] }>(r))

export const setPromptPresets = (presets: PromptPreset[]) =>
  fetch('/api/settings/prompt-presets', {
    method: 'PUT',
    headers: jsonHeaders,
    body: JSON.stringify({ presets }),
  }).then((r) => json<{ presets: PromptPreset[] }>(r))

// --- providers ---

export const listProviderTypes = () =>
  fetch('/api/provider-types').then((r) => json<ProviderType[]>(r))

export const listProviders = () =>
  fetch('/api/providers').then((r) => json<ProviderInstance[]>(r))

export const createProvider = (typeId: string, name: string, config: Record<string, string>) =>
  fetch('/api/providers', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ type_id: typeId, name, config }),
  }).then((r) => json<ProviderInstance>(r))

export const updateProvider = (id: string, name: string, config: Record<string, string>) =>
  fetch(`/api/providers/${id}`, {
    method: 'PATCH',
    headers: jsonHeaders,
    body: JSON.stringify({ name, config }),
  }).then((r) => json<ProviderInstance>(r))

export const deleteProvider = (id: string) =>
  fetch(`/api/providers/${id}`, { method: 'DELETE' }).then((r) => json<{ ok: boolean }>(r))

export const checkProvider = (id: string) =>
  fetch(`/api/providers/${id}/check`, { method: 'POST' }).then((r) =>
    json<{ ok: boolean; detail: string }>(r),
  )

export const listProviderModels = (id: string) =>
  fetch(`/api/providers/${id}/models`).then((r) => json<ModelInfo[]>(r))

// --- MCP servers ---

export const listMcpServers = () =>
  fetch('/api/mcp-servers').then((r) => json<McpServer[]>(r))

export const createMcpServer = (
  name: string,
  command: string,
  args: string[],
  env: Record<string, string>,
) =>
  fetch('/api/mcp-servers', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ name, command, args, env }),
  }).then((r) => json<McpServer>(r))

export const deleteMcpServer = (id: string) =>
  fetch(`/api/mcp-servers/${id}`, { method: 'DELETE' }).then((r) => json<{ ok: boolean }>(r))

export const setMcpServerEnabled = (id: string, enabled: boolean) =>
  fetch(`/api/mcp-servers/${id}`, {
    method: 'PATCH',
    headers: jsonHeaders,
    body: JSON.stringify({ enabled }),
  }).then((r) => json<McpServer>(r))

// --- approvals (agent shell gate) ---

export const resolveApproval = (approvalId: string, approve: boolean) =>
  fetch(`/api/approvals/${approvalId}`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ approve }),
  }).then((r) => json<{ ok: boolean }>(r))

// --- chat streaming ---

export interface GenParams {
  temperature?: number
  max_tokens?: number
}

export interface ChatRequest {
  conversation_id?: string
  provider_id: string
  model: string
  /** omit to continue/regenerate from existing history */
  message?: string
  agent_mode: boolean
  system_prompt?: string
  /** server-side rewind-then-respond with rollback on empty failure */
  regenerate?: boolean
  params?: GenParams
}

export interface ResearchRequest {
  provider_id: string
  model: string
  question: string
  params?: GenParams
}

export async function streamResearch(
  req: ResearchRequest,
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamNdjson('/api/research', req, onEvent, signal)
}

// --- blind arena ---

export interface CompleteRequest {
  provider_id: string
  model: string
  message: string
  params?: GenParams
}

export async function streamComplete(
  req: CompleteRequest,
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamNdjson('/api/complete', req, onEvent, signal)
}

export type ArenaWinner = 'a' | 'b' | 'tie' | 'both_bad'

export interface ArenaStanding {
  model: string
  games: number
  wins: number
  losses: number
  ties: number
  win_rate: number
}

export const recordArenaVote = (modelA: string, modelB: string, winner: ArenaWinner) =>
  fetch('/api/arena/vote', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ model_a: modelA, model_b: modelB, winner }),
  }).then((r) => json<{ ok: boolean }>(r))

export const getArenaLeaderboard = () =>
  fetch('/api/arena/leaderboard').then((r) => json<ArenaStanding[]>(r))

export const resetArenaLeaderboard = () =>
  fetch('/api/arena/leaderboard', { method: 'DELETE' }).then((r) => json<{ deleted: number }>(r))

// --- writing editor ---

export interface DocumentSummary {
  id: string
  title: string
  chars: number
  created_at: string
  updated_at: string
}

export interface Document {
  id: string
  title: string
  content: string
  created_at: string
  updated_at: string
}

export const listDocuments = () =>
  fetch('/api/documents').then((r) => json<DocumentSummary[]>(r))

export const getDocument = (id: string) =>
  fetch(`/api/documents/${id}`).then((r) => json<Document>(r))

export const createDocument = (title = 'Untitled', content = '') =>
  fetch('/api/documents', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ title, content }),
  }).then((r) => json<Document>(r))

export const updateDocument = (id: string, patch: { title?: string; content?: string }) =>
  fetch(`/api/documents/${id}`, {
    method: 'PUT',
    headers: jsonHeaders,
    body: JSON.stringify(patch),
  }).then((r) => json<Document>(r))

export const deleteDocument = (id: string) =>
  fetch(`/api/documents/${id}`, { method: 'DELETE' }).then((r) => json<{ ok: boolean }>(r))

export interface EditRequest {
  provider_id: string
  model: string
  instruction: string
  selection?: string
  context?: string
  params?: GenParams
}

export async function streamEdit(
  req: EditRequest,
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamNdjson('/api/documents/edit', req, onEvent, signal)
}

async function streamNdjson(
  url: string,
  body: unknown,
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(url, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(body),
    signal,
  })
  if (!resp.ok || !resp.body) {
    const text = await resp.text()
    throw new Error(errorMessage(resp.status, text))
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (!line.trim()) continue
      onEvent(JSON.parse(line) as StreamEvent)
    }
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer) as StreamEvent)
}

export async function streamChat(
  req: ChatRequest,
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamNdjson('/api/chat', req, onEvent, signal)
}
