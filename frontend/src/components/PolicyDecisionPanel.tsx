import type { TaskEvent } from '../types'

interface PolicyDecisionPanelProps {
  events: TaskEvent[]
}

const POLICY_KEYWORDS = ['policy', 'permission', 'authorization', 'security', 'decision']

export function PolicyDecisionPanel({ events }: PolicyDecisionPanelProps) {
  const decisions = events.filter(event => {
    const haystack = `${event.stage} ${event.event_type} ${event.actor_type}`.toLowerCase()
    return POLICY_KEYWORDS.some(keyword => haystack.includes(keyword))
  })

  if (!decisions.length) {
    return <div className="empty-state">暂无策略决策</div>
  }

  return (
    <div className="policy-panel">
      {decisions.map(event => (
        <div key={event.event_id} className={`policy-row ${event.status}`}>
          <div className="timeline-meta">
            <strong>{event.stage}</strong>
            <span>{event.actor_type}</span>
            <time>{new Date(event.created_at).toLocaleTimeString()}</time>
          </div>
          <p>{event.message}</p>
          <pre>{JSON.stringify(event.payload, null, 2)}</pre>
        </div>
      ))}
    </div>
  )
}
