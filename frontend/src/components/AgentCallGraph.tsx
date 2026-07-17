import type { AgentCard, TaskEvent } from '../types'

interface AgentCallGraphProps {
  agents?: AgentCard[]
  events: TaskEvent[]
}

export function AgentCallGraph({ agents = [], events }: AgentCallGraphProps) {
  const agentIds = Array.from(new Set([
    ...events.map(event => event.actor_id).filter(Boolean),
    ...agents.map(agent => agent.agent_id),
  ])).slice(0, 12)

  if (!agentIds.length) {
    return <div className="empty-state">暂无 Agent 调用</div>
  }

  return (
    <div className="call-graph">
      {agentIds.map((agentId, index) => {
        const card = agents.find(agent => agent.agent_id === agentId)
        return (
          <div key={agentId} className="call-node">
            <span>{index + 1}</span>
            <div>
              <strong>{card?.display_name || agentId}</strong>
              <small>{card?.sub_ioa_id || events.find(event => event.actor_id === agentId)?.actor_type || 'runtime'}</small>
            </div>
          </div>
        )
      })}
    </div>
  )
}
