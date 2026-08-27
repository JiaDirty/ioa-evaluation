import { useState } from 'react'
import { ArtifactViewer } from '../components/ArtifactViewer'
import { Card } from '../components/Card'
import { ErrorBanner } from '../components/ErrorBanner'
import { Timeline } from '../components/Timeline'
import { createTask, getTaskEvents } from '../api/client'
import type { TaskEvent, TaskResponse } from '../types'

export function TaskWorkspace() {
  const [prompt, setPrompt] = useState('为下个月前往肯尼亚出差的公司高管安排行程，评估当地健康风险，并比较合适的旅行保险；任何购买必须先确认。')
  const [maxPlanNodes, setMaxPlanNodes] = useState(12)
  const [maxDelegationDepth, setMaxDelegationDepth] = useState(4)
  const [requireCitations, setRequireCitations] = useState(true)
  const [humanApproval, setHumanApproval] = useState(true)
  const [result, setResult] = useState<TaskResponse | null>(null)
  const [events, setEvents] = useState<TaskEvent[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async () => {
    setLoading(true)
    setError('')
    try {
      const response = await createTask({
        prompt,
        user_goal: '生成可追溯的结构化结果',
        execution_mode: 'agentic',
        constraints: {
          max_plan_nodes: maxPlanNodes,
          max_delegation_depth: maxDelegationDepth,
          require_citations: requireCitations,
          human_approval_for_side_effects: humanApproval,
          allow_cross_domain_relay: true,
        },
      })
      setResult(response)
      setEvents(await getTaskEvents(response.task_id))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  const openDetail = () => {
    if (result?.task_id) {
      window.location.hash = `task-detail:${encodeURIComponent(result.task_id)}`
    }
  }

  return (
    <div className="task-workspace">
      {error && <ErrorBanner message={error} onRetry={submit} />}
      <div className="workspace-grid">
        <Card title="任务提交">
          <div className="workspace-form">
            <label>
              <span>Prompt</span>
              <textarea value={prompt} onChange={e => setPrompt(e.target.value)} rows={6} />
            </label>
            <div className="workspace-row">
              <label>
                <span>计划节点上限</span>
                <input type="number" min={1} max={24} value={maxPlanNodes} onChange={e => setMaxPlanNodes(Number(e.target.value))} />
              </label>
              <label>
                <span>委托深度上限</span>
                <input type="number" min={1} max={8} value={maxDelegationDepth} onChange={e => setMaxDelegationDepth(Number(e.target.value))} />
              </label>
            </div>
            <div className="workspace-row compact">
              <label className="checkbox-label">
                <input type="checkbox" checked={requireCitations} onChange={e => setRequireCitations(e.target.checked)} />
                <span>需要来源</span>
              </label>
              <label className="checkbox-label">
                <input type="checkbox" checked={humanApproval} onChange={e => setHumanApproval(e.target.checked)} />
                <span>人工确认</span>
              </label>
            </div>
            <button className="btn-primary" onClick={submit} disabled={loading || !prompt.trim()}>
              {loading ? '运行中...' : '提交任务'}
            </button>
          </div>
        </Card>

        <Card title="执行结果">
          {result ? (
            <div className="result-panel">
              <div className="result-kpis">
                <div><strong>{result.status}</strong><span>状态</span></div>
                <div><strong>{result.participating_agents.length}</strong><span>Agent</span></div>
                <div><strong>{result.artifacts.length}</strong><span>产物</span></div>
              </div>
              <div className="result-id">task: {result.task_id}</div>
              <div className="result-id">trace: {result.trace_id}</div>
              {result.error && <div className="error-banner-text">{result.error}</div>}
              <pre className="artifact-json">{JSON.stringify(result.output, null, 2)}</pre>
              <button className="btn-secondary" onClick={openDetail}>查看任务详情</button>
            </div>
          ) : (
            <div className="empty-state">等待任务</div>
          )}
        </Card>
      </div>

      <div className="grid-2">
        <Card title="Timeline">
          <Timeline events={events} />
        </Card>
        <Card title="Artifacts">
          <ArtifactViewer artifacts={result?.artifacts || []} />
        </Card>
      </div>
    </div>
  )
}
