import { useEffect, useState } from 'react'
import { AgentCallGraph } from '../components/AgentCallGraph'
import { ArtifactViewer } from '../components/ArtifactViewer'
import { Card } from '../components/Card'
import { ErrorBanner } from '../components/ErrorBanner'
import { ExecutionGraph } from '../components/ExecutionGraph'
import { LiveEventStream } from '../components/LiveEventStream'
import { PolicyDecisionPanel } from '../components/PolicyDecisionPanel'
import { RetryControls } from '../components/RetryControls'
import { Timeline } from '../components/Timeline'
import { ToolHistoryPanel } from '../components/ToolHistoryPanel'
import { getAgentRegistry, getTaskDetail, getTaskExecutionGraph, getTaskToolCalls } from '../api/client'
import type { AgentCard, ExecutionGraph as ExecutionGraphModel, TaskDetail as TaskDetailModel, ToolCallRecord } from '../types'

interface TaskDetailProps {
  initialTaskId?: string
}

export function TaskDetail({ initialTaskId = '' }: TaskDetailProps) {
  const [taskId, setTaskId] = useState(initialTaskId)
  const [detail, setDetail] = useState<TaskDetailModel | null>(null)
  const [graph, setGraph] = useState<ExecutionGraphModel | null>(null)
  const [toolCalls, setToolCalls] = useState<ToolCallRecord[]>([])
  const [agents, setAgents] = useState<AgentCard[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const load = async (id = taskId) => {
    if (!id.trim()) return
    setLoading(true)
    setError('')
    try {
      const [taskDetail, registry, executionGraph, calls] = await Promise.all([
        getTaskDetail(id.trim()),
        getAgentRegistry(),
        getTaskExecutionGraph(id.trim()),
        getTaskToolCalls(id.trim()),
      ])
      setDetail(taskDetail)
      setAgents(registry)
      setGraph(executionGraph)
      setToolCalls(calls.length ? calls : taskDetail.tool_calls || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setTaskId(initialTaskId)
    if (initialTaskId) void load(initialTaskId)
  }, [initialTaskId])

  return (
    <div className="task-workspace">
      {error && <ErrorBanner message={error} onRetry={() => load()} />}
      <Card title="任务查询">
        <div className="detail-search">
          <input value={taskId} onChange={event => setTaskId(event.target.value)} placeholder="task_id" />
          <button className="btn-primary" onClick={() => load()} disabled={loading || !taskId.trim()}>
            {loading ? '查询中' : '查询'}
          </button>
        </div>
      </Card>

      {detail ? (
        <>
          <div className="grid-2">
            <Card title="Prompt">
              <pre className="artifact-json">{String(detail.task.prompt || detail.task.description || '')}</pre>
            </Card>
            <Card title="TaskSpec">
              <pre className="artifact-json">
                {JSON.stringify(extractTaskSpec(detail.response.artifacts), null, 2)}
              </pre>
            </Card>
          </div>
          <div className="result-kpis">
            <Card><strong>{detail.response.status}</strong><span>状态</span></Card>
            <Card><strong>{detail.events.length}</strong><span>事件</span></Card>
            <Card><strong>{(detail.artifacts || detail.response.artifacts).length}</strong><span>产物</span></Card>
            <Card><strong>{toolCalls.length}</strong><span>工具调用</span></Card>
          </div>
          <Card title="任务操作">
            <RetryControls taskId={detail.task_id} status={detail.response.status} onChanged={() => load(detail.task_id)} />
          </Card>
          <Card title="Execution Graph">
            <ExecutionGraph graph={graph} />
          </Card>
          <div className="grid-2">
            <Card title="Agent 调用链">
              <AgentCallGraph events={detail.events} agents={agents} />
            </Card>
            <Card title="策略决策">
              <PolicyDecisionPanel events={detail.events} />
            </Card>
          </div>
          <div className="grid-2">
            <Card title="Live Events">
              <LiveEventStream events={detail.events} />
            </Card>
            <Card title="Tool Calls">
              <ToolHistoryPanel calls={toolCalls} />
            </Card>
          </div>
          <div className="grid-2">
            <Card title="Timeline">
              <Timeline events={detail.events} />
            </Card>
            <Card title="Artifacts">
              <ArtifactViewer artifacts={detail.artifacts || detail.response.artifacts} />
            </Card>
          </div>
          <Card title="Task Envelope">
            <pre className="artifact-json">{JSON.stringify(detail, null, 2)}</pre>
          </Card>
        </>
      ) : (
        <Card>
          <div className="empty-state">暂无任务详情</div>
        </Card>
      )}
    </div>
  )
}

function extractTaskSpec(artifacts: unknown[]) {
  for (let index = artifacts.length - 1; index >= 0; index -= 1) {
    const artifact = artifacts[index] as { metadata?: Record<string, unknown> }
    if (artifact?.metadata?.task_spec) return artifact.metadata.task_spec
  }
  return {}
}
