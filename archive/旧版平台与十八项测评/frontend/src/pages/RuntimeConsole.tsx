import { useEffect, useMemo, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  type Edge,
  type Node,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  Activity,
  Boxes,
  Clock3,
  GitBranch,
  Network,
  Play,
  Radio,
  RefreshCw,
  Search,
  ShieldCheck,
  Wrench,
} from 'lucide-react'
import { createTask, getSystemGraph, getTaskObservability, getTaskSpan } from '../api/client'
import { useTaskStream } from '../hooks/useTaskStream'
import type {
  ExecutionGraph,
  InteractionEdge,
  ObservabilitySpan,
  SpanDetail,
  SystemGraph,
  SystemGraphNode,
  TaskEvent,
  TaskObservability,
} from '../types'

type GraphMode = 'execution' | 'interaction'
type ViewMode = 'simple' | 'detail'

const DEFAULT_PROMPT = '为下个月前往肯尼亚出差的公司高管安排行程，评估当地健康风险，并比较合适的旅行保险；任何购买必须先确认。'

export function RuntimeConsole() {
  const [executionMode, setExecutionMode] = useState<'offline_deterministic' | 'agentic_live'>('offline_deterministic')
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT)
  const [taskId, setTaskId] = useState<string | null>(() => window.localStorage.getItem('ioa:lastTaskId'))
  const [lookupId, setLookupId] = useState(() => window.localStorage.getItem('ioa:lastTaskId') || '')
  const [system, setSystem] = useState<SystemGraph | null>(null)
  const [observation, setObservation] = useState<TaskObservability | null>(null)
  const [selectedSpan, setSelectedSpan] = useState<SpanDetail | null>(null)
  const [selectedSystemNode, setSelectedSystemNode] = useState<SystemGraphNode | null>(null)
  const [graphMode, setGraphMode] = useState<GraphMode>('execution')
  const [systemMode, setSystemMode] = useState<'overview' | 'full'>('overview')
  const [bottomMode, setBottomMode] = useState<'events' | 'messages' | 'tools' | 'artifacts'>('events')
  const [viewMode, setViewMode] = useState<ViewMode>('simple')
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const stream = useTaskStream(taskId)

  const loadSystem = async (mode = executionMode) => {
    try {
      setSystem(await getSystemGraph(mode))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }

  const refresh = async (id = taskId) => {
    if (!id) return
    try {
      const data = await getTaskObservability(id)
      setObservation(data)
      const terminal = ['completed', 'failed', 'cancelled'].includes(data.task.status)
      const active = terminal
        ? [...data.spans].reverse().find(span => span.status !== 'started')
        : [...data.spans].reverse().find(span => span.status === 'started' || span.status === 'waiting')
      const selected = active || data.spans[data.spans.length - 1]
      if (selected && !selectedSpan) void selectSpan(selected.span_id, id)
    } catch (caught) {
      if (caught instanceof Error && caught.message.includes('404')) {
        window.localStorage.removeItem('ioa:lastTaskId')
        setTaskId(null)
        setLookupId('')
        setObservation(null)
        setError('上次任务记录已被清理，请重新运行任务或输入有效的任务编号。')
      } else {
        setError(humanError(caught))
      }
    }
  }

  useEffect(() => { void loadSystem() }, [executionMode])
  useEffect(() => {
    if (taskId) window.localStorage.setItem('ioa:lastTaskId', taskId)
  }, [taskId])
  useEffect(() => {
    if (!taskId) return
    const timer = window.setTimeout(() => void refresh(taskId), 120)
    return () => window.clearTimeout(timer)
  }, [taskId, stream.events.length, stream.status])
  useEffect(() => {
    if (['completed', 'failed', 'cancelled'].includes(stream.status)) setRunning(false)
  }, [stream.status])

  const startTask = async () => {
    setError('')
    setRunning(true)
    setObservation(null)
    setSelectedSpan(null)
    setSelectedSystemNode(null)
    try {
      const response = await createTask({
        prompt,
        user_goal: '生成可追溯的结构化结果',
        execution_mode: executionMode,
        async_mode: true,
        constraints: {
          max_plan_nodes: 12,
          max_delegation_depth: 4,
          require_citations: true,
          human_approval_for_side_effects: true,
          allow_cross_domain_relay: true,
        },
      })
      setTaskId(response.task_id)
      setLookupId(response.task_id)
    } catch (caught) {
      setRunning(false)
      setError(humanError(caught))
    }
  }

  const openTask = () => {
    const id = lookupId.trim()
    if (!id) return
    setTaskId(id)
    setSelectedSpan(null)
    setSelectedSystemNode(null)
    void refresh(id)
  }

  const selectSpan = async (spanId: string, id = taskId) => {
    if (!id) return
    try {
      setSelectedSpan(await getTaskSpan(id, spanId))
      setSelectedSystemNode(null)
    } catch (caught) {
      setError(humanError(caught))
    }
  }

  const completed = observation?.execution_graph.nodes.filter(node => ['completed', 'skipped'].includes(node.status)).length || 0
  const total = observation?.execution_graph.nodes.length || 0
  const progress = total ? Math.round((completed / total) * 100) : 0
  const latestEvent = observation?.events[observation.events.length - 1]
  const activeComponentIds = useMemo(() => new Set(
    observation?.spans
      .filter(span => span.status === 'started' || span.status === 'waiting')
      .flatMap(span => [span.component_id, ...span.upstream_ids, ...span.downstream_ids])
      .filter(Boolean) || [],
  ), [observation])
  const effectiveSystemMode = viewMode === 'simple' ? 'overview' : systemMode
  const systemGraph = useMemo(() => buildSystemFlow(system, activeComponentIds, effectiveSystemMode, viewMode === 'simple'), [system, activeComponentIds, effectiveSystemMode, viewMode])
  const effectiveGraphMode = viewMode === 'simple' ? 'execution' : graphMode
  const runtimeGraph = useMemo(() => effectiveGraphMode === 'execution'
    ? buildExecutionFlow(observation?.execution_graph || null, viewMode === 'simple')
    : buildInteractionFlow(observation?.interaction_edges || [], system),
  [effectiveGraphMode, observation, system, viewMode])

  const onRuntimeNodeClick = (_: unknown, node: Node) => {
    const spanId = String(node.data?.spanId || '')
    if (spanId) void selectSpan(spanId)
  }
  const onRuntimeEdgeClick = (_: unknown, edge: Edge) => {
    const spanId = String(edge.data?.spanId || '')
    if (spanId) void selectSpan(spanId)
  }

  return (
    <div className={`runtime-console ${viewMode === 'simple' ? 'runtime-simple' : ''}`}>
      <section className="runtime-commandbar">
        <div className="runtime-title">
          <div className="runtime-live-icon"><Activity size={18} /></div>
          <div><h2>IoA运行过程</h2><p>查看任务经过了哪些环节，以及每一步的结果</p></div>
        </div>
        <div className="runtime-control-groups">
          <div className="runtime-mode-control" aria-label="显示方式">
            <button className={viewMode === 'simple' ? 'active' : ''} onClick={() => setViewMode('simple')}>简洁</button>
            <button className={viewMode === 'detail' ? 'active' : ''} onClick={() => setViewMode('detail')}>详细</button>
          </div>
          <div className="runtime-mode-control" aria-label="运行模式">
            <button className={executionMode === 'offline_deterministic' ? 'active' : ''} onClick={() => setExecutionMode('offline_deterministic')}>离线演示</button>
            <button className={executionMode === 'agentic_live' ? 'active' : ''} onClick={() => setExecutionMode('agentic_live')}>真实测评</button>
          </div>
        </div>
        <div className="runtime-status-strip">
          <span className={`runtime-connection ${stream.connected ? 'online' : ''}`}><Radio size={14} />{stream.connected ? '实时连接' : stream.status === 'completed' ? '回放完成' : '未连接'}</span>
          <span><Clock3 size={14} />{latestEvent ? stageLabel(latestEvent.stage) : '等待任务'}</span>
          <strong>{statusLabel(stream.status)}</strong>
        </div>
      </section>

      <section className="runtime-launcher">
        <textarea value={prompt} onChange={event => setPrompt(event.target.value)} rows={2} aria-label="任务输入" />
        <button className="btn-primary runtime-run" onClick={startTask} disabled={running || !prompt.trim()}>
          {running ? <RefreshCw size={16} className="spin" /> : <Play size={16} />}
          {running ? '运行中' : '运行任务'}
        </button>
        <div className="runtime-lookup">
          <Search size={15} />
          <input value={lookupId} onChange={event => setLookupId(event.target.value)} placeholder="输入历史任务编号" />
          <button title="打开任务" onClick={openTask}>打开</button>
        </div>
      </section>

      {error && <div className="runtime-error">{error}</div>}

      <section className={`runtime-kpis ${viewMode === 'simple' ? 'runtime-kpis-simple' : ''}`}>
        <RuntimeKpi icon={<Activity size={16} />} label="任务状态" value={statusLabel(observation?.task.status || stream.status)} />
        <RuntimeKpi icon={<GitBranch size={16} />} label="完成进度" value={`${progress}%`} detail={`${completed}/${total} 个步骤`} />
        <RuntimeKpi icon={<Clock3 size={16} />} label="当前环节" value={latestEvent ? stageLabel(latestEvent.stage) : '等待任务'} />
        {viewMode === 'detail' && <RuntimeKpi icon={<Boxes size={16} />} label="参与组件" value={String(new Set(observation?.spans.map(span => span.component_id).filter(Boolean)).size || 0)} />}
        {viewMode === 'detail' && <RuntimeKpi icon={<Wrench size={16} />} label="工具调用" value={String(observation?.tool_calls.length || 0)} />}
        {viewMode === 'detail' && <RuntimeKpi icon={<ShieldCheck size={16} />} label="审计事件" value={String(observation?.events.length || 0)} />}
      </section>

      <section className="runtime-main-grid">
        <div className="runtime-panel system-panel">
          <PanelHeader icon={<Network size={16} />} title="IoA内部结构" action={<div className="runtime-header-actions">{viewMode === 'detail' && <div className="runtime-segments"><button className={systemMode === 'overview' ? 'active' : ''} onClick={() => setSystemMode('overview')}>主要环节</button><button className={systemMode === 'full' ? 'active' : ''} onClick={() => setSystemMode('full')}>所有组件</button></div>}<button title="刷新结构" onClick={() => loadSystem()}><RefreshCw size={14} /></button></div>} />
          {viewMode === 'simple' && <div className="runtime-structure-legend"><span className="core">公共服务</span><span className="domain">领域网络</span><span className="access">网关与注册</span></div>}
          <div className="runtime-system-canvas">
            <ReactFlow
              nodes={systemGraph.nodes}
              edges={systemGraph.edges}
              onNodeClick={(_, node) => {
                const source = system?.nodes.find(item => item.id === node.id) || null
                setSelectedSystemNode(source)
                setSelectedSpan(null)
              }}
              fitView
              minZoom={0.15}
              maxZoom={1.5}
            >
              <Background gap={18} size={1} />
              <Controls showInteractive={false} />
            </ReactFlow>
          </div>
        </div>

        <div className="runtime-panel execution-panel">
          <PanelHeader
            icon={<GitBranch size={16} />}
            title={effectiveGraphMode === 'execution' ? '任务执行流程' : '组件调用关系'}
            action={
              viewMode === 'detail' ? <div className="runtime-segments">
                <button className={graphMode === 'execution' ? 'active' : ''} onClick={() => setGraphMode('execution')}>执行步骤</button>
                <button className={graphMode === 'interaction' ? 'active' : ''} onClick={() => setGraphMode('interaction')}>调用关系</button>
              </div> : undefined
            }
          />
          <div className="runtime-execution-canvas">
            {runtimeGraph.nodes.length ? (
              <ReactFlow
                nodes={runtimeGraph.nodes}
                edges={runtimeGraph.edges}
                onNodeClick={onRuntimeNodeClick}
                onEdgeClick={onRuntimeEdgeClick}
                fitView
                minZoom={0.2}
                maxZoom={1.6}
              >
                <Background gap={20} size={1} />
                {viewMode === 'detail' && <MiniMap pannable zoomable nodeColor={node => statusColor(String(node.data?.status || 'pending'))} />}
                <Controls showInteractive={false} />
              </ReactFlow>
            ) : <EmptyGraph />}
          </div>
        </div>

        <div className="runtime-panel inspector-panel">
          <PanelHeader icon={<Boxes size={16} />} title="这一步做了什么" />
          <StepInspector span={selectedSpan} systemNode={selectedSystemNode} detailed={viewMode === 'detail'} />
        </div>
      </section>

      <section className="runtime-panel runtime-bottom">
        <div className="runtime-bottom-tabs">
          {viewMode === 'simple' ? <strong>运行记录</strong> : (['events', 'messages', 'tools', 'artifacts'] as const).map(mode => (
            <button key={mode} className={bottomMode === mode ? 'active' : ''} onClick={() => setBottomMode(mode)}>
              {({ events: '运行记录', messages: '智能体消息', tools: '工具调用', artifacts: '结果文件' })[mode]}
            </button>
          ))}
        </div>
        <BottomPanel mode={viewMode === 'simple' ? 'events' : bottomMode} observation={observation} onSelectSpan={selectSpan} simple={viewMode === 'simple'} />
      </section>
    </div>
  )
}

function RuntimeKpi({ icon, label, value, detail }: { icon: ReactNode; label: string; value: string; detail?: string }) {
  return <div className="runtime-kpi"><span>{icon}{label}</span><strong>{value || '-'}</strong>{detail && <small>{detail}</small>}</div>
}

function PanelHeader({ icon, title, action }: { icon: ReactNode; title: string; action?: ReactNode }) {
  return <div className="runtime-panel-header"><div>{icon}<strong>{title}</strong></div>{action}</div>
}

function EmptyGraph() {
  return <div className="runtime-empty"><GitBranch size={28} /><strong>等待执行数据</strong><span>运行任务或打开历史任务后显示</span></div>
}

function StepInspector({ span, systemNode, detailed }: { span: SpanDetail | null; systemNode: SystemGraphNode | null; detailed: boolean }) {
  if (systemNode) {
    return <div className="runtime-inspector"><StatusPill status={systemNode.status} /><h3>{systemLabel(systemNode)}</h3><p>{componentTypeLabel(systemNode.type)}</p>{detailed && <><p>{systemNode.id}</p><JsonSection title="技术信息" value={systemNode.metadata} /></>}</div>
  }
  if (!span) return <div className="runtime-empty inspector-empty"><Search size={24} /><strong>选择步骤或组件</strong><span>查看输入、输出、状态和错误</span></div>
  const item = span.span
  return (
    <div className="runtime-inspector">
      <div className="runtime-inspector-title"><StatusPill status={item.status} /><span>#{item.sequence}</span></div>
      <h3>{eventMessage(String(item.metadata.message || item.operation))}</h3>
      <p>{componentTypeLabel(item.component_type)}</p>
      <dl className="runtime-facts">
        <div><dt>当前状态</dt><dd>{statusLabel(item.status)}</dd></div>
        <div><dt>耗时</dt><dd>{formatDuration(item.duration_ms)}</dd></div>
        <div><dt>开始</dt><dd>{formatTime(item.started_at)}</dd></div>
        <div><dt>结束</dt><dd>{formatTime(item.ended_at)}</dd></div>
        {detailed && <div><dt>技术步骤</dt><dd>{item.operation}</dd></div>}
        {detailed && <div><dt>组件编号</dt><dd>{item.component_id || item.node_id || '-'}</dd></div>}
      </dl>
      {item.error && <div className="runtime-step-error">{item.error}</div>}
      <JsonSection title="收到的内容" value={item.input} open={detailed} />
      <JsonSection title="产生的结果" value={item.output} open={detailed} />
      {detailed && <JsonSection title="技术信息" value={item.metadata} />}
    </div>
  )
}

function JsonSection({ title, value, open = false }: { title: string; value: unknown; open?: boolean }) {
  return <details className="runtime-json" open={open}><summary>{title}</summary><pre>{JSON.stringify(value || {}, null, 2)}</pre></details>
}

function BottomPanel({ mode, observation, onSelectSpan, simple }: {
  mode: 'events' | 'messages' | 'tools' | 'artifacts'
  observation: TaskObservability | null
  onSelectSpan: (spanId: string) => void
  simple: boolean
}) {
  if (!observation) return <div className="runtime-bottom-empty">暂无运行数据</div>
  if (mode === 'tools') return <div className="runtime-records">{observation.tool_calls.map(call => <div key={call.call_id}><b>{call.tool_id}</b><span>{call.caller_agent_id}</span><StatusPill status={call.status} /><code>{compact(call.arguments)}</code><code>{compact(call.result)}</code></div>)}</div>
  if (mode === 'artifacts') return <div className="runtime-records">{observation.artifacts.map((artifact, index) => <div key={String(artifact.artifact_id || index)}><b>{String(artifact.artifact_type || 'artifact')}</b><span>{String(artifact.producer_agent_id || '')}</span><code>{compact(artifact.content)}</code></div>)}</div>
  if (mode === 'messages') {
    return <div className="runtime-records">{observation.interaction_edges.map(edge => <button key={edge.edge_id} onClick={() => onSelectSpan(edge.span_id)}><b>{edge.source_id} → {edge.target_id}</b><span>{edge.relation}{edge.protocol ? ` · ${edge.protocol}` : ''}</span><StatusPill status={edge.status} /><code>{edge.message || '-'}</code></button>)}</div>
  }
  const events = simple ? observation.events.slice(-12) : observation.events
  return <div className="runtime-event-list">{events.map(event => <button key={event.event_id} onClick={() => event.span_id && onSelectSpan(event.span_id)}><time>{formatTime(event.created_at)}</time><StatusPill status={event.phase || event.status} /><strong>{stageLabel(event.stage)}</strong><span>{eventMessage(event.message || event.event_type)}</span></button>)}</div>
}

function StatusPill({ status }: { status: string }) {
  return <span className={`runtime-status-pill ${statusClass(status)}`}>{statusLabel(status)}</span>
}

function buildSystemFlow(graph: SystemGraph | null, active: Set<string>, mode: 'overview' | 'full', simple: boolean): { nodes: Node[]; edges: Edge[] } {
  if (!graph) return { nodes: [], edges: [] }
  const visibleTypes = new Set(['ioa', 'marketplace', 'registry', 'protocol', 'knowledge', 'audit', 'synthesis', 'judge', 'human', 'sub_ioa', 'gateway'])
  const visibleNodes = mode === 'overview' ? graph.nodes.filter(node => visibleTypes.has(node.type)) : graph.nodes
  const visibleIds = new Set(visibleNodes.map(node => node.id))
  const groups = [
    visibleNodes.filter(node => node.type === 'ioa'),
    visibleNodes.filter(node => node.parent_id === 'ioa' && !['ioa', 'sub_ioa', 'tool', 'mcp'].includes(node.type)),
    visibleNodes.filter(node => node.type === 'sub_ioa'),
    visibleNodes.filter(node => node.type === 'gateway'),
    visibleNodes.filter(node => node.type === 'registry' && node.parent_id !== 'ioa'),
    visibleNodes.filter(node => node.type === 'agent'),
    visibleNodes.filter(node => node.type === 'tool' || node.type === 'mcp'),
  ].filter(group => group.length)
  const positions = new Map<string, { x: number; y: number }>()
  let y = 0
  groups.forEach((group, groupIndex) => {
    const columns = groupIndex === 0 ? 1 : 4
    const rows = Math.ceil(group.length / columns)
    group.forEach((node, index) => {
      positions.set(node.id, {
        x: groupIndex === 0 ? 270 : (index % columns) * 180,
        y: y + Math.floor(index / columns) * 88,
      })
    })
    y += rows * 88 + (groupIndex === 0 ? 38 : 62)
  })
  const nodes = visibleNodes.map(node => {
    const isActive = active.has(node.id) || active.has(node.id.replace('tool:', ''))
    return {
      id: node.id,
      position: positions.get(node.id) || { x: 0, y },
      sourcePosition: Position.Bottom,
      targetPosition: Position.Top,
      data: { label: systemLabel(node), status: isActive ? 'started' : node.status },
      style: systemNodeStyle(node.type, isActive),
    }
  })
  const structuralRelation = (source: SystemGraphNode | undefined, target: SystemGraphNode | undefined, relation: string) => (
    relation === 'contains' ||
    (source?.id === 'marketplace' && target?.type === 'sub_ioa') ||
    (source?.type === 'gateway' && target?.type === 'registry')
  )
  const nodeById = new Map(visibleNodes.map(node => [node.id, node]))
  const edges = graph.edges
    .filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target))
    .filter(edge => !simple || structuralRelation(nodeById.get(edge.source), nodeById.get(edge.target), edge.relation))
    .map(edge => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: 'smoothstep',
      label: simple ? undefined : relationLabel(edge.relation),
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
      animated: active.has(edge.source) || active.has(edge.target),
      style: { stroke: '#8c959f', strokeWidth: 1.25 },
    }))
  return { nodes, edges }
}

function buildExecutionFlow(graph: ExecutionGraph | null, simple: boolean): { nodes: Node[]; edges: Edge[] } {
  if (!graph) return { nodes: [], edges: [] }
  const byId = new Map(graph.nodes.map(node => [node.node_id, node]))
  const nodes = graph.nodes.map((node, index) => {
    const row = Math.floor(index / 2)
    const column = simple && row % 2 === 1 ? 1 - (index % 2) : index % 2
    return {
      id: node.node_id,
      position: { x: column * 230, y: row * 115 },
      sourcePosition: simple
        ? (index % 4 === 0 ? Position.Right : index % 4 === 1 ? Position.Bottom : index % 4 === 2 ? Position.Left : Position.Bottom)
        : Position.Bottom,
      targetPosition: simple
        ? (index % 4 === 0 ? Position.Top : index % 4 === 1 ? Position.Left : index % 4 === 2 ? Position.Top : Position.Right)
        : Position.Top,
      data: { label: executionLabel(node.label), status: node.status, spanId: String(node.metadata?.span_id || '') },
      style: nodeStyle(node.status),
    }
  })
  const edges = simple ? graph.nodes.slice(1).map((node, index) => ({
    id: `simple-${index}`,
    source: graph.nodes[index].node_id,
    target: node.node_id,
    type: 'smoothstep',
    markerEnd: { type: MarkerType.ArrowClosed },
    animated: node.status === 'running',
  })) : graph.edges.map((edge, index) => ({
    id: `execution-${index}-${edge.source}-${edge.target}`,
    source: edge.source,
    target: edge.target,
    type: 'smoothstep',
    label: relationLabel(edge.edge_type),
    markerEnd: { type: MarkerType.ArrowClosed },
    animated: byId.get(edge.target)?.status === 'running',
  }))
  return { nodes, edges }
}

function buildInteractionFlow(edgesData: InteractionEdge[], system: SystemGraph | null): { nodes: Node[]; edges: Edge[] } {
  const ids = Array.from(new Set(edgesData.flatMap(edge => [edge.source_id, edge.target_id])))
  const label = new Map(system?.nodes.map(node => [node.id, node.label]) || [])
  const columns = Math.max(1, Math.ceil(Math.sqrt(ids.length)))
  const nodes = ids.map((id, index) => ({
    id,
    position: { x: (index % columns) * 220, y: Math.floor(index / columns) * 125 },
    data: { label: label.has(id) ? systemLabel({ id, label: label.get(id)!, type: '' }) : componentIdLabel(id), status: latestStatus(id, edgesData) },
    style: nodeStyle(latestStatus(id, edgesData)),
  }))
  const edges = edgesData.map(edge => ({
    id: edge.edge_id,
    source: edge.source_id,
    target: edge.target_id,
    label: relationLabel(edge.relation),
    data: { spanId: edge.span_id },
    animated: edge.status === 'started',
    markerEnd: { type: MarkerType.ArrowClosed },
  }))
  return { nodes, edges }
}

function latestStatus(id: string, edges: InteractionEdge[]) {
  return [...edges].reverse().find(edge => edge.source_id === id || edge.target_id === id)?.status || 'completed'
}

function nodeStyle(status: string, compact = false): CSSProperties {
  const color = statusColor(status)
  return {
    border: `2px solid ${color}`,
    background: status === 'started' || status === 'running' ? '#ddf4ff' : '#ffffff',
    color: '#1f2328',
    borderRadius: 6,
    width: compact ? 130 : 180,
    minHeight: compact ? 46 : 58,
    padding: compact ? 7 : 9,
    fontSize: compact ? 10 : 11,
    fontWeight: 600,
    boxShadow: status === 'started' || status === 'running' ? `0 0 0 3px ${color}22` : '0 1px 2px #1f232814',
  }
}

function systemNodeStyle(type: string, active: boolean): CSSProperties {
  const palettes: Record<string, { border: string; background: string }> = {
    ioa: { border: '#24292f', background: '#f6f8fa' },
    marketplace: { border: '#0969da', background: '#ddf4ff' },
    protocol: { border: '#0969da', background: '#ddf4ff' },
    knowledge: { border: '#0969da', background: '#ddf4ff' },
    audit: { border: '#0969da', background: '#ddf4ff' },
    synthesis: { border: '#0969da', background: '#ddf4ff' },
    judge: { border: '#0969da', background: '#ddf4ff' },
    human: { border: '#0969da', background: '#ddf4ff' },
    sub_ioa: { border: '#9a6700', background: '#fff8c5' },
    gateway: { border: '#bc4c00', background: '#fff1e5' },
    registry: { border: '#1a7f37', background: '#dafbe1' },
    agent: { border: '#8250df', background: '#fbefff' },
    tool: { border: '#57606a', background: '#f6f8fa' },
    mcp: { border: '#57606a', background: '#f6f8fa' },
  }
  const palette = palettes[type] || palettes.tool
  return {
    border: `2px solid ${active ? '#0969da' : palette.border}`,
    background: palette.background,
    color: '#1f2328',
    borderRadius: 6,
    width: 150,
    minHeight: 48,
    padding: 8,
    fontSize: 10,
    fontWeight: 700,
    textAlign: 'center',
    boxShadow: active ? '0 0 0 3px #0969da22' : '0 1px 2px #1f232814',
  }
}

function statusColor(status: string) {
  if (status === 'failed' || status === 'denied') return '#cf222e'
  if (status === 'waiting') return '#bf8700'
  if (status === 'started' || status === 'running') return '#0969da'
  if (status === 'completed' || status === 'active') return '#1a7f37'
  if (status === 'cancelled' || status === 'skipped') return '#8c959f'
  return '#d0d7de'
}

function statusClass(status: string) {
  if (status === 'failed' || status === 'denied') return 'danger'
  if (status === 'waiting') return 'warning'
  if (status === 'started' || status === 'running') return 'running'
  if (status === 'completed' || status === 'active') return 'success'
  return 'muted'
}

const STATUS_LABELS: Record<string, string> = {
  idle: '尚未开始', connecting: '正在连接', queued: '等待运行', pending: '等待运行',
  started: '进行中', running: '进行中', waiting: '等待确认', completed: '已完成',
  active: '正常', failed: '失败', denied: '已阻止', skipped: '已跳过', cancelled: '已取消',
}

const STAGE_LABELS: Record<string, string> = {
  task_runner: '任务运行', received: '接收任务', specifying: '理解任务', planning: '制定计划',
  executing: '执行任务', local_discovery: '寻找智能体', global_discovery: '全局寻找',
  candidate_ranking: '选择智能体', policy: '安全检查', protocol: '协议通信',
  agent_runtime: '智能体处理', agent_runtime_loop: '智能体处理', tool: '调用工具',
  synthesis: '汇总结果', synthesizing: '汇总结果', judging: '结果裁判', judge: '结果裁判', completed: '任务完成',
}

const CAPABILITY_LABELS: Record<string, string> = {
  itinerary_planning: '规划行程', logistics: '安排行程细节', public_health: '评估健康风险',
  travel_insurance: '比较旅行保险', risk_assessment: '综合风险评估', financial_analysis: '财务分析',
  clinical_analysis: '医疗分析', flight_search: '查询航班', news_aggregation: '汇总信息',
  fact_checking: '核实信息', general_analysis: '综合分析',
}

function statusLabel(status?: string | null) {
  if (!status) return '未知'
  return STATUS_LABELS[status.toLowerCase()] || status
}

function stageLabel(stage?: string | null) {
  if (!stage) return '等待任务'
  return STAGE_LABELS[stage] || componentTypeLabel(stage)
}

function componentTypeLabel(type?: string | null) {
  const labels: Record<string, string> = {
    ioa: 'IoA系统', marketplace: '任务分发中心', registry: '注册中心', gateway: '任务网关',
    protocol: '协议路由', knowledge: '共享知识库', audit: '审计中心', synthesis: '结果汇总',
    judge: '结果裁判', human: '人工确认', sub_ioa: '领域子网络', agent: '智能体',
    agent_runtime: '智能体运行环境', task_runner: '任务运行器', orchestrator: '任务调度器',
    policy_check: '安全规则检查', tool: '工具', mcp: '外部工具服务', llm: '模型调用',
  }
  return labels[type || ''] || (type ? type.replace(/_/g, ' ') : '系统组件')
}

function systemLabel(node: Pick<SystemGraphNode, 'id' | 'label' | 'type'>) {
  const exact: Record<string, string> = {
    ioa: 'IoA系统', marketplace: '任务分发中心', 'global-registry': '全局注册中心',
    'protocol-router': '协议路由', 'shared-knowledge': '共享知识库', 'global-audit': '全局审计中心',
    'synthesis-agent': '结果汇总', judge: '结果裁判', 'human-checkpoint': '人工确认',
  }
  if (exact[node.id]) return exact[node.id]
  const domain = node.id.split('-')[0]
  const domains: Record<string, string> = { finance: '金融', healthcare: '医疗', travel: '出行', news: '信息' }
  if (domains[domain]) {
    if (node.type === 'sub_ioa') return `${domains[domain]}子网络`
    if (node.type === 'gateway') return `${domains[domain]}任务网关`
    if (node.type === 'registry') return `${domains[domain]}注册中心`
  }
  return node.label || componentIdLabel(node.id)
}

function componentIdLabel(id: string) {
  if (id.endsWith('-gw')) return `${domainLabel(id.split('-')[0])}任务网关`
  if (id.startsWith('capability-')) return '任务步骤'
  if (id === 'policy-precheck') return '安全规则检查'
  if (id === 'synthesis') return '结果汇总'
  return id
}

function domainLabel(domain: string) {
  return ({ finance: '金融', healthcare: '医疗', travel: '出行', news: '信息' } as Record<string, string>)[domain] || ''
}

function executionLabel(label: string): string {
  if (label === 'TaskSpec validation and policy precheck') return '理解任务并检查安全规则'
  if (label.startsWith('Capability task:')) {
    const capability = label.split(':').slice(1).join(':').trim()
    return CAPABILITY_LABELS[capability] || `完成任务：${capability.replace(/_/g, ' ')}`
  }
  if (label.startsWith('Human checkpoint:')) return '等待人工确认'
  if (label === 'Synthesize sourced final answer') return '汇总各方结果'
  return eventMessage(label)
}

function relationLabel(relation: string) {
  const labels: Record<string, string> = {
    contains: '包含', routes: '分发', discovers: '查找', registers: '注册', dispatches: '调用',
    negotiates: '协商', audits: '审计', provides: '提供', connects: '连接', depends_on: '完成后',
    call: '调用', delegates: '委托', produces: '生成', consumes: '使用',
  }
  return labels[relation] || labels[relation.replace('dep_', '')] || relation
}

function eventMessage(message: string): string {
  const exact: Record<string, string> = {
    'Task queued': '任务已进入等待队列', 'Task runner started': '开始运行任务',
    'Agentic task received': '已收到任务', 'Building TaskSpec': '正在理解任务要求',
    'TaskSpec ready for planning': '任务要求已整理完成',
    'Capability-level plan created without bound Agent IDs': '已生成执行计划',
    'Entry Gateway selected from capability/domain context': '已选择合适的任务网关',
    'Agent capability node completed': '当前任务步骤已完成', 'Synthesizing artifacts': '正在汇总各方结果',
    'Task runner finished': '任务运行完成', 'Agentic task completed': '任务处理完成',
  }
  if (exact[message]) return exact[message]
  if (message.startsWith('Started: ')) return `开始：${executionLabel(message.slice(9))}`
  if (message.startsWith('Completed: ')) return `完成：${executionLabel(message.slice(11))}`
  if (message.startsWith('Discovered ')) return '已找到可处理该步骤的智能体'
  if (message.startsWith('Selected ')) return '已选择合适的智能体'
  if (message.startsWith('Agent runtime started')) return '智能体开始处理'
  if (message.startsWith('Agent runtime completed')) return '智能体处理完成'
  if (message.startsWith('Agent turn ')) return '智能体本轮处理完成'
  return message.replace(/_/g, ' ')
}

function formatDuration(value?: number | null) {
  if (value == null) return '-'
  return value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(2)} s`
}

function formatTime(value?: string | null) {
  if (!value) return '-'
  return new Date(value).toLocaleTimeString()
}

function compact(value: unknown) {
  const text = JSON.stringify(value ?? {})
  return text.length > 240 ? `${text.slice(0, 240)}…` : text
}

function humanError(caught: unknown) {
  const message = caught instanceof Error ? caught.message : String(caught)
  if (message.includes('404')) return '没有找到对应的任务记录，请检查任务编号。'
  if (message.includes('500')) return '服务处理失败，请查看后端日志。'
  if (message.includes('Failed to fetch')) return '无法连接后端服务，请确认服务已经启动。'
  return `运行出现问题：${message}`
}
