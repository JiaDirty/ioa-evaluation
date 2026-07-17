import { useEffect, useState } from 'react'
import { Card } from '../components/Card'
import { ErrorBanner } from '../components/ErrorBanner'
import { McpServerPanel } from '../components/McpServerPanel'
import { getMcpServers, syncMcpServerTools, syncMcpTools } from '../api/client'
import type { McpServerConfig } from '../types'

export function McpRegistry() {
  const [servers, setServers] = useState<McpServerConfig[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [syncingId, setSyncingId] = useState('')
  const [lastSync, setLastSync] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      setServers(await getMcpServers())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const syncAll = async () => {
    setSyncingId('all')
    setError('')
    try {
      const result = await syncMcpTools()
      setLastSync(`all: ${result.synced}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSyncingId('')
    }
  }

  const syncOne = async (serverId: string) => {
    setSyncingId(serverId)
    setError('')
    try {
      const result = await syncMcpServerTools(serverId)
      setLastSync(`${result.server_id}: ${result.synced}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSyncingId('')
    }
  }

  return (
    <div className="registry-page">
      {error && <ErrorBanner message={error} onRetry={load} />}
      <div className="registry-toolbar">
        <div className="result-id">MCP servers: {servers.length}{lastSync ? ` · synced ${lastSync}` : ''}</div>
        <div className="detail-actions">
          <button className="btn-secondary" onClick={load} disabled={loading}>{loading ? '刷新中' : '刷新'}</button>
          <button className="btn-primary" onClick={syncAll} disabled={syncingId === 'all'}>{syncingId === 'all' ? '同步中' : '同步全部 enabled'}</button>
        </div>
      </div>
      <Card title="MCP Server 注册表">
        <McpServerPanel servers={servers} syncingId={syncingId} onSync={syncOne} />
      </Card>
    </div>
  )
}
