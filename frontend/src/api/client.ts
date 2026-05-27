import type { ExperimentReport, ReportSummary, FeedbackSummary, FeedbackAction, SubIoA, TopologyData, RiskTestFlowDoc } from '../types'

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
export const getReport = (id: string, signal?: AbortSignal) => fetchJSON<ExperimentReport>(`/api/experiments/reports/${id}`, signal)
export const runExperiment = (body: { mode: string; category?: string; test_id?: string; topology?: string }) =>
  postJSON<{ experiment_id: string; status: string }>('/api/experiments/run', body)

export const getFeedbackSummary = (signal?: AbortSignal) => fetchJSON<FeedbackSummary>('/api/feedback/summary', signal)
export const getFeedbackActions = (signal?: AbortSignal) => fetchJSON<FeedbackAction[]>('/api/feedback/actions', signal)

export const getSubIoAs = (signal?: AbortSignal) => fetchJSON<SubIoA[]>('/api/agents/sub-ioas', signal)
export const getTopology = (signal?: AbortSignal) => fetchJSON<TopologyData>('/api/agents/topology', signal)
export const updateTopology = (style: string) =>
  fetch(`${BASE}/api/agents/topology?style=${style}`, { method: 'PUT' }).then(r => r.json()) as Promise<TopologyData>

export const getRiskTestFlowsDoc = (signal?: AbortSignal) => fetchJSON<RiskTestFlowDoc>('/api/docs/risk-tests', signal)
