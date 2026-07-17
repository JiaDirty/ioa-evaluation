import type { TaskEvent } from '../types'

interface TimelineProps {
  events: TaskEvent[]
}

export function Timeline({ events }: TimelineProps) {
  if (!events.length) {
    return <div className="empty-state">暂无事件</div>
  }
  return (
    <div className="timeline">
      {events.map(event => (
        <div key={event.event_id} className={`timeline-item ${event.status}`}>
          <div className="timeline-dot" />
          <div className="timeline-body">
            <div className="timeline-meta">
              <strong>{event.stage}</strong>
              <span>{event.event_type}</span>
              <time>{new Date(event.created_at).toLocaleTimeString()}</time>
            </div>
            <p>{event.message}</p>
            <small>{event.actor_type} · {event.actor_id}</small>
          </div>
        </div>
      ))}
    </div>
  )
}
