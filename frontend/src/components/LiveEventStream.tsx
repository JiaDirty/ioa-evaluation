import type { TaskEvent } from '../types'

interface LiveEventStreamProps {
  events: TaskEvent[]
}

export function LiveEventStream({ events }: LiveEventStreamProps) {
  if (!events.length) {
    return <div className="empty-state">暂无实时事件</div>
  }
  return (
    <div className="live-event-stream">
      {events.map(event => (
        <div key={event.event_id} className={`live-event ${event.status}`}>
          <time>{new Date(event.created_at).toLocaleTimeString()}</time>
          <strong>{event.stage}</strong>
          <span>{event.event_type}</span>
          <p>{event.message}</p>
        </div>
      ))}
    </div>
  )
}
