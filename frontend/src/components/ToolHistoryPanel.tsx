import type { ToolCallRecord } from '../types'

interface ToolHistoryPanelProps {
  calls: ToolCallRecord[]
}

export function ToolHistoryPanel({ calls }: ToolHistoryPanelProps) {
  if (!calls.length) {
    return <div className="empty-state">暂无任务工具调用</div>
  }
  return (
    <div className="tool-call-panel">
      {calls.map(call => (
        <div key={call.call_id} className={`tool-call-row ${call.status}`}>
          <div>
            <strong>{call.tool_id}</strong>
            <span>{call.status}</span>
          </div>
          <small>{call.caller_agent_id} · {new Date(call.created_at).toLocaleString()}</small>
          {call.error && <p>{call.error}</p>}
          <pre>{JSON.stringify({ arguments: call.arguments, result: call.result }, null, 2)}</pre>
        </div>
      ))}
    </div>
  )
}
