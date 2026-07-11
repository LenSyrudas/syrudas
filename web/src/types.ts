export interface ToolCall {
  id: string
  name: string
  arguments: Record<string, unknown>
}

export interface DbMessage {
  id: string
  role: 'user' | 'assistant' | 'tool' | 'system'
  content: string
  tool_calls: ToolCall[] | null
  tool_call_id: string | null
  created_at: string
}

export interface Conversation {
  id: string
  title: string
  provider_id: string | null
  model: string | null
  agent_mode: number
  system_prompt: string
  created_at: string
  updated_at: string
  messages?: DbMessage[]
}

export interface StreamEvent {
  type:
    | 'meta'
    | 'text_delta'
    | 'tool_call'
    | 'tool_result'
    | 'approval_required'
    | 'usage'
    | 'error'
    | 'done'
  text?: string
  tool_call?: ToolCall
  conversation_id?: string
  title?: string
  approval_id?: string
  tool_call_id?: string
  name?: string
  content?: string
  message?: string
  input_tokens?: number
  output_tokens?: number
}

export interface ConfigField {
  key: string
  label: string
  type: 'text' | 'password' | 'url'
  required: boolean
  default: string
  placeholder: string
}

export interface ProviderType {
  type_id: string
  display_name: string
  config_fields: ConfigField[]
}

export interface ProviderInstance {
  id: string
  type_id: string
  name: string
  config: Record<string, string>
}

export interface ModelInfo {
  id: string
  name?: string | null
}

export interface McpServer {
  id: string
  name: string
  command: string
  args: string[]
  env: Record<string, string>
  enabled: number
}
