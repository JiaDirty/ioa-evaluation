import { useEffect, useMemo, useState } from 'react'
import { Card } from '../components/Card'
import { ErrorBanner } from '../components/ErrorBanner'
import { ToolCallPanel } from '../components/ToolCallPanel'
import { callTool, getToolHistory, getTools } from '../api/client'
import type { ToolDescriptor, ToolResult } from '../types'

const SAMPLE_ARGS: Record<string, Record<string, unknown>> = {
  get_stock_price: { ticker: 'AAPL' },
  analyze_financial_report: { company: 'Apple Inc.' },
  lookup_drug_info: { drug_name: 'aspirin' },
  check_clinical_trial: { trial_id: 'NCT04280705' },
  search_flights: { origin: 'PVG', destination: 'PEK', date: '2026-07-10' },
  search_hotels: { city: 'Shanghai', checkin: '2026-07-10', checkout: '2026-07-12' },
  aggregate_news: { topic: 'AI safety', days: 3 },
  fact_check: { claim: 'AI systems can autonomously execute every financial trade without approval.' },
}

export function ToolRegistry() {
  const [tools, setTools] = useState<ToolDescriptor[]>([])
  const [history, setHistory] = useState<ToolResult[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [risk, setRisk] = useState('all')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [calling, setCalling] = useState(false)

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const [toolData, historyData] = await Promise.all([getTools(), getToolHistory()])
      setTools(toolData)
      setHistory(historyData)
      if (!selectedId && toolData.length) setSelectedId(toolData[0].tool_id)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const filtered = useMemo(
    () => tools.filter(tool => risk === 'all' || tool.risk_level === risk),
    [tools, risk],
  )
  const selected = filtered.find(tool => tool.tool_id === selectedId) || filtered[0]
  const selectedHistory = selected ? history.filter(item => item.tool_id === selected.tool_id) : history
  const risks = ['all', 'low', 'medium', 'high', 'critical']

  const runSample = async () => {
    if (!selected) return
    setCalling(true)
    setError('')
    try {
      await callTool(selected.tool_id, {
        caller_agent_id: 'console',
        granted_scopes: [...selected.required_scopes, selected.risk_level === 'high' || selected.risk_level === 'critical' ? 'high_risk_tool' : ''].filter(Boolean),
        arguments: SAMPLE_ARGS[selected.tool_id] || {},
      })
      setHistory(await getToolHistory())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setCalling(false)
    }
  }

  return (
    <div className="registry-page">
      {error && <ErrorBanner message={error} onRetry={load} />}
      <div className="registry-toolbar">
        <div className="pill-group">
          {risks.map(item => (
            <button key={item} className={`pill ${risk === item ? 'active' : ''}`} onClick={() => setRisk(item)}>
              {item}
            </button>
          ))}
        </div>
        <button className="btn-secondary" onClick={load} disabled={loading}>{loading ? '刷新中' : '刷新'}</button>
      </div>

      <div className="registry-grid">
        <Card title="Tool 列表">
          <div className="entity-list">
            {filtered.map(tool => (
              <button key={tool.tool_id} className={selected?.tool_id === tool.tool_id ? 'active' : ''} onClick={() => setSelectedId(tool.tool_id)}>
                <strong>{tool.name}</strong>
                <span>{tool.provider} · {tool.risk_level}</span>
              </button>
            ))}
          </div>
        </Card>

        <Card title="Tool Descriptor">
          {selected ? (
            <div className="registry-detail">
              <div className="detail-title-row">
                <div>
                  <h3>{selected.name}</h3>
                  <span>{selected.tool_id}</span>
                </div>
                <span className={`risk-chip ${selected.risk_level}`}>{selected.risk_level}</span>
              </div>
              <p className="detail-copy">{selected.description}</p>
              <div className="detail-grid">
                <div><strong>{selected.provider}</strong><span>provider</span></div>
                <div><strong>{selected.timeout_seconds}s</strong><span>timeout</span></div>
                <div><strong>{selected.required_scopes.length}</strong><span>scopes</span></div>
                <div><strong>{selectedHistory.length}</strong><span>calls</span></div>
              </div>
              <section>
                <h4>Scopes</h4>
                <div className="token-list">{selected.required_scopes.map(scope => <span key={scope}>{scope}</span>)}</div>
              </section>
              <div className="detail-actions">
                <button className="btn-primary" onClick={runSample} disabled={calling}>{calling ? '调用中' : '样例调用'}</button>
              </div>
              <pre className="artifact-json">{JSON.stringify(selected, null, 2)}</pre>
            </div>
          ) : (
            <div className="empty-state">暂无 Tool</div>
          )}
        </Card>
      </div>

      <Card title="调用历史">
        <ToolCallPanel calls={selectedHistory} />
      </Card>
    </div>
  )
}
