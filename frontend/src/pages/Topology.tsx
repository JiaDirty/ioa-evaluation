import { useMemo } from 'react'
import { ReactFlow, Background, Controls, type Node, type Edge } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Card } from '../components/Card'
import { Spinner } from '../components/Spinner'
import { ErrorBanner } from '../components/ErrorBanner'
import { useApi } from '../hooks/useApi'
import { getSubIoAs, getTopology, updateTopology } from '../api/client'

const COLORS: Record<string, string> = {
  finance: '#0969da',
  healthcare: '#1a7f37',
  travel: '#e3b341',
  news: '#8250df',
}

const LABELS: Record<string, string> = {
  finance: 'Finance\n金融',
  healthcare: 'Healthcare\n医疗',
  travel: 'Travel\n旅行',
  news: 'News\n新闻',
}

export function Topology() {
  const { data: subIoAs, loading: subLoading, error: subError, reload: reloadSub } = useApi(
    (signal) => getSubIoAs(signal),
    []
  )
  const { data: topology, loading: topoLoading, error: topoError, reload: reloadTopo } = useApi(
    (signal) => getTopology(signal),
    []
  )

  const nodes: Node[] = useMemo(() => {
    if (!topology) return []
    const positions: Record<string, { x: number; y: number }> = {
      finance: { x: 200, y: 30 },
      healthcare: { x: 30, y: 200 },
      travel: { x: 370, y: 200 },
      news: { x: 200, y: 370 },
    }
    return topology.nodes.map(id => ({
      id,
      position: positions[id] || { x: 0, y: 0 },
      data: { label: LABELS[id] || id },
      style: {
        background: '#ddf4ff',
        border: `2px solid ${COLORS[id] || '#0969da'}`,
        borderRadius: '50%',
        width: 100,
        height: 100,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        textAlign: 'center' as const,
        fontSize: 12,
        fontWeight: 600,
        color: COLORS[id] || '#0969da',
        whiteSpace: 'pre-line' as const,
      },
    }))
  }, [topology])

  const edges: Edge[] = useMemo(() => {
    if (!topology) return []
    return topology.edges.map((e, i) => ({
      id: `e-${i}`,
      source: e.source,
      target: e.target,
      style: { stroke: '#54aeff', strokeWidth: 2, strokeDasharray: '5,5' },
    }))
  }, [topology])

  const handleTopologyChange = async (style: string) => {
    await updateTopology(style)
    reloadTopo()
  }

  if (subLoading || topoLoading) return <Spinner />
  if (subError) return <ErrorBanner message={`加载子IoA失败: ${subError}`} onRetry={reloadSub} />
  if (topoError) return <ErrorBanner message={`加载拓扑失败: ${topoError}`} onRetry={reloadTopo} />

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr', gap: 16 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {(subIoAs || []).map(s => (
          <Card key={s.id}>
            <div style={{ fontWeight: 600, color: COLORS[s.id] || 'var(--color-blue)' }}>{s.name}</div>
            <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4 }}>
              能力: {s.capabilities.slice(0, 3).join(', ')}...
            </div>
            <div style={{ fontSize: 11, color: 'var(--color-green)', marginTop: 2 }}>
              AG2: {s.agent_name} ✓
            </div>
          </Card>
        ))}

        <Card title="拓扑模式">
          <div className="pill-group" style={{ flexDirection: 'column' }}>
            {['full_mesh', 'star', 'chain'].map(t => (
              <span
                key={t}
                className={`pill ${topology?.style === t ? 'active' : ''}`}
                onClick={() => handleTopologyChange(t)}
                style={{ textAlign: 'center' }}
              >
                {t === 'full_mesh' ? '全连接' : t === 'star' ? '星型' : '链式'}
              </span>
            ))}
          </div>
        </Card>
      </div>

      <Card title="拓扑图" style={{ minHeight: 500 }}>
        <div style={{ height: 460 }}>
          <ReactFlow nodes={nodes} edges={edges} fitView>
            <Background />
            <Controls />
          </ReactFlow>
        </div>
      </Card>
    </div>
  )
}
