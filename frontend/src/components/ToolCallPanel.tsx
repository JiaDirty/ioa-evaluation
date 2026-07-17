import type { ToolResult } from '../types'

interface ToolCallPanelProps {
  calls: ToolResult[]
}

export function ToolCallPanel({ calls }: ToolCallPanelProps) {
  if (!calls.length) {
    return <div className="empty-state">暂无工具调用</div>
  }

  return (
    <div className="tool-call-panel">
      {calls.map(call => (
        <div key={call.call_id} className={`tool-call-row ${call.status}`}>
          <div>
            <strong>{call.tool_id}</strong>
            <span>{call.status}</span>
          </div>
          <small>{new Date(call.created_at).toLocaleString()}</small>
          {call.error && <p>{call.error}</p>}
          {call.output !== undefined && <pre>{JSON.stringify(call.output, null, 2)}</pre>}
        </div>
      ))}
    </div>
  )
}
