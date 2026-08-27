import { useEffect, useMemo, useState } from 'react'
import { Card } from '../components/Card'
import { ErrorBanner } from '../components/ErrorBanner'
import { getAgentRegistry } from '../api/client'
import type { AgentCard } from '../types'

const SUB_IOAS = ['all', 'finance', 'healthcare', 'travel', 'news']
const STATUSES = ['all', 'active', 'suspended', 'revoked']

export function AgentRegistry() {
  const [agents, setAgents] = useState<AgentCard[]>([])
  const [subIoA, setSubIoA] = useState('all')
  const [status, setStatus] = useState('all')
  const [selectedId, setSelectedId] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const data = await getAgentRegistry({
        subIoaId: subIoA === 'all' ? undefined : subIoA,
        includeInactive: true,
      })
      setAgents(data)
      if (!selectedId && data.length) setSelectedId(data[0].agent_id)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [subIoA])

  const filtered = useMemo(
    () => agents.filter(agent => status === 'all' || agent.status === status),
    [agents, status],
  )
  const selected = filtered.find(agent => agent.agent_id === selectedId) || filtered[0]
  const counts = useMemo(() => ({
    total: agents.length,
    active: agents.filter(agent => agent.status === 'active').length,
    suspended: agents.filter(agent => agent.status === 'suspended').length,
    domains: new Set(agents.map(agent => agent.sub_ioa_id)).size,
  }), [agents])

  return (
    <div className="registry-page">
      {error && <ErrorBanner message={error} onRetry={load} />}
      <div className="registry-toolbar">
        <div className="pill-group">
          {SUB_IOAS.map(item => (
            <button key={item} className={`pill ${subIoA === item ? 'active' : ''}`} onClick={() => setSubIoA(item)}>
              {item}
            </button>
          ))}
        </div>
        <div className="pill-group">
          {STATUSES.map(item => (
            <button key={item} className={`pill ${status === item ? 'active' : ''}`} onClick={() => setStatus(item)}>
              {item}
            </button>
          ))}
        </div>
        <button className="btn-secondary" onClick={load} disabled={loading}>{loading ? '刷新中' : '刷新'}</button>
      </div>

      <div className="registry-kpis">
        <Card><strong>{counts.total}</strong><span>全部 Agent</span></Card>
        <Card><strong>{counts.active}</strong><span>active</span></Card>
        <Card><strong>{counts.suspended}</strong><span>suspended</span></Card>
        <Card><strong>{counts.domains}</strong><span>Sub-IoA</span></Card>
      </div>

      <div className="registry-grid">
        <Card title="Agent 列表">
          <div className="entity-list">
            {filtered.map(agent => (
              <button key={agent.agent_id} className={selected?.agent_id === agent.agent_id ? 'active' : ''} onClick={() => setSelectedId(agent.agent_id)}>
                <strong>{agent.display_name}</strong>
                <span>{agent.sub_ioa_id} · {agent.status}</span>
              </button>
            ))}
          </div>
        </Card>

        <Card title="Agent Card">
          {selected ? (
            <div className="registry-detail">
              <div className="detail-title-row">
                <div>
                  <h3>{selected.display_name}</h3>
                  <span>{selected.agent_id}</span>
                </div>
                <span className={`status-chip ${selected.status}`}>{selected.status}</span>
              </div>
              <div className="detail-grid">
                <div><strong>{selected.provider}</strong><span>provider</span></div>
                <div><strong>{selected.sub_ioa_id}</strong><span>Sub-IoA</span></div>
                <div><strong>{selected.trust_level}</strong><span>trust</span></div>
                <div><strong>{selected.reputation_score.toFixed(2)}</strong><span>reputation</span></div>
              </div>
              <section>
                <h4>Capabilities</h4>
                <div className="token-list">{selected.declared_capabilities.map(cap => <span key={cap}>{cap}</span>)}</div>
              </section>
              <section>
                <h4>Protocols</h4>
                <div className="token-list">{selected.supported_protocols.map(protocol => <span key={protocol}>{protocol}</span>)}</div>
              </section>
              <section>
                <h4>Permissions</h4>
                <div className="token-list">{selected.permission_scope.map(scope => <span key={scope}>{scope}</span>)}</div>
              </section>
              <pre className="artifact-json">{JSON.stringify(selected, null, 2)}</pre>
            </div>
          ) : (
            <div className="empty-state">暂无 Agent</div>
          )}
        </Card>
      </div>
    </div>
  )
}
