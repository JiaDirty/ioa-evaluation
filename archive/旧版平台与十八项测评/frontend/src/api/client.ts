import type {
  ExperimentReport,
  ReportSummary,
  FeedbackSummary,
  FeedbackAction,
  SubIoA,
  TopologyData,
  RiskTestFlowDoc,
  AgentCard,
  ExecutionGraph,
  McpServerConfig,
  TaskCreateRequest,
  TaskDetail,
  TaskEvent,
  TaskResponse,
  ToolCallRecord,
  ToolDescriptor,
  ToolResult,
  SystemGraph,
  TaskObservability,
  SpanDetail,
} from '../types'

const BASE = ''

async function fetchJSON<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${url}`, { signal })
  if (!res.ok) throw new Error(`API error: ${res.status}`)
  return res.json()
}

async function postJSON<T>(url: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok) throw new Error(`API error: ${res.status}`)
  return res.json()
}

export const listReports = (signal?: AbortSignal) => fetchJSON<ReportSummary[]>('/api/experiments/reports', signal)
export const getReport = (id: string, signal?: AbortSignal) =>
  fetchJSON<ExperimentReport>(`/api/experiments/reports/${encodeURIComponent(id)}`, signal)
export const runExperiment = (body: {
  mode: string
  category?: string
  test_id?: string
  topology?: string
  execution_mode?: 'agentic' | 'agentic_live' | 'scripted' | 'offline_deterministic'
}) =>
  postJSON<{ experiment_id: string; status: string }>('/api/experiments/run', body)

export const getFeedbackSummary = (signal?: AbortSignal) => fetchJSON<FeedbackSummary>('/api/feedback/summary', signal)
export const getFeedbackActions = (signal?: AbortSignal) => fetchJSON<FeedbackAction[]>('/api/feedback/actions', signal)

export const getSubIoAs = (signal?: AbortSignal) => fetchJSON<SubIoA[]>('/api/agents/sub-ioas', signal)
export const getTopology = (signal?: AbortSignal) => fetchJSON<TopologyData>('/api/agents/topology', signal)
export const updateTopology = (style: string) =>
  fetch(`${BASE}/api/agents/topology?style=${style}`, { method: 'PUT' }).then(r => r.json()) as Promise<TopologyData>

export const getRiskTestFlowsDoc = (signal?: AbortSignal) => fetchJSON<RiskTestFlowDoc>('/api/docs/risk-tests', signal)

export const createTask = (body: TaskCreateRequest) => postJSON<TaskResponse>('/api/tasks', body)
export const getTask = (taskId: string, signal?: AbortSignal) =>
  fetchJSON<TaskResponse>(`/api/tasks/${taskId}`, signal)
export const getTaskDetail = (taskId: string, signal?: AbortSignal) =>
  fetchJSON<TaskDetail>(`/api/tasks/${taskId}/detail`, signal)
export const getTaskEvents = (taskId: string, signal?: AbortSignal) =>
  fetchJSON<TaskEvent[]>(`/api/tasks/${taskId}/events`, signal)
export const getTaskExecutionGraph = (taskId: string, signal?: AbortSignal) =>
  fetchJSON<ExecutionGraph>(`/api/tasks/${taskId}/execution-graph`, signal)
export const getTaskToolCalls = (taskId: string, signal?: AbortSignal) =>
  fetchJSON<ToolCallRecord[]>(`/api/tasks/${taskId}/tool-calls`, signal)
export const getSystemGraph = (executionMode = 'offline_deterministic', signal?: AbortSignal) =>
  fetchJSON<SystemGraph>(`/api/system/graph?execution_mode=${encodeURIComponent(executionMode)}`, signal)
export const getTaskObservability = (taskId: string, signal?: AbortSignal) =>
  fetchJSON<TaskObservability>(`/api/tasks/${encodeURIComponent(taskId)}/observability`, signal)
export const getTaskSpans = (taskId: string, afterSequence = 0, signal?: AbortSignal) =>
  fetchJSON<import('../types').ObservabilitySpan[]>(
    `/api/tasks/${encodeURIComponent(taskId)}/spans?after_sequence=${afterSequence}`,
    signal,
  )
export const getTaskSpan = (taskId: string, spanId: string, signal?: AbortSignal) =>
  fetchJSON<SpanDetail>(
    `/api/tasks/${encodeURIComponent(taskId)}/spans/${encodeURIComponent(spanId)}`,
    signal,
  )
export const cancelTask = (taskId: string) => postJSON<{ task_id: string; status: string }>(`/api/tasks/${taskId}/cancel`, {})
export const retryTask = (taskId: string) => postJSON<TaskResponse>(`/api/tasks/${taskId}/retry`, { mode: 'full' })

export const getAgentRegistry = (params: { subIoaId?: string; includeInactive?: boolean } = {}, signal?: AbortSignal) => {
  const search = new URLSearchParams()
  if (params.subIoaId) search.set('sub_ioa_id', params.subIoaId)
  if (params.includeInactive !== undefined) search.set('include_inactive', String(params.includeInactive))
  const suffix = search.toString() ? `?${search.toString()}` : ''
  return fetchJSON<AgentCard[]>(`/api/agents/registry${suffix}`, signal)
}

export const getTools = (signal?: AbortSignal) => fetchJSON<ToolDescriptor[]>('/api/tools', signal)
export const getTool = (toolId: string, signal?: AbortSignal) => fetchJSON<ToolDescriptor>(`/api/tools/${toolId}`, signal)
export const getToolHistory = (signal?: AbortSignal) => fetchJSON<ToolResult[]>('/api/tools/history', signal)
export const callTool = (
  toolId: string,
  body: { arguments?: Record<string, unknown>; granted_scopes?: string[]; caller_agent_id?: string; task_id?: string; trace_id?: string },
) => postJSON<ToolResult>(`/api/tools/${toolId}/call`, body)

export const getMcpServers = (signal?: AbortSignal) => fetchJSON<McpServerConfig[]>('/api/mcp/servers', signal)
export const syncMcpTools = () => postJSON<{ synced: number }>('/api/mcp/sync-tools', {})
export const syncMcpServerTools = (serverId: string) =>
  postJSON<{ server_id: string; synced: number }>(`/api/mcp/servers/${serverId}/sync-tools`, {})
