import type { McpServerConfig } from '../types'

interface McpServerPanelProps {
  servers: McpServerConfig[]
  syncingId: string
  onSync: (serverId: string) => void
}

export function McpServerPanel({ servers, syncingId, onSync }: McpServerPanelProps) {
  if (!servers.length) {
    return <div className="empty-state">暂无 MCP Server</div>
  }

  return (
    <div className="mcp-server-list">
      {servers.map(server => (
        <div key={server.server_id} className={`mcp-server ${server.enabled ? 'active' : 'suspended'}`}>
          <div className="detail-title-row">
            <div>
              <h3>{server.name}</h3>
              <span>{server.server_id}</span>
            </div>
            <span className={`risk-chip ${server.risk_level}`}>{server.risk_level}</span>
          </div>
          <div className="detail-grid">
            <div><strong>{server.enabled ? 'enabled' : 'disabled'}</strong><span>状态</span></div>
            <div><strong>{server.transport}</strong><span>transport</span></div>
            <div><strong>{server.timeout_seconds}s</strong><span>timeout</span></div>
            <div><strong>{server.allowed_tools.length}</strong><span>allowlist</span></div>
          </div>
          <p className="detail-copy">{server.endpoint || server.command || 'not configured'}</p>
          <div className="token-list">{server.allowed_tools.map(tool => <span key={tool}>{tool}</span>)}</div>
          <button className="btn-secondary" onClick={() => onSync(server.server_id)} disabled={!server.enabled || syncingId === server.server_id}>
            {syncingId === server.server_id ? '同步中' : '同步工具'}
          </button>
        </div>
      ))}
    </div>
  )
}
