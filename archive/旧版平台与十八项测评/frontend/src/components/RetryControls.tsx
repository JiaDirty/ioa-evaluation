import { useState } from 'react'
import { cancelTask, retryTask } from '../api/client'

interface RetryControlsProps {
  taskId: string
  status: string
  onChanged: () => void
}

export function RetryControls({ taskId, status, onChanged }: RetryControlsProps) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const canCancel = ['queued', 'running', 'pending', 'in_progress', 'cancel_requested'].includes(status)
  const canRetry = ['failed', 'cancelled'].includes(status)

  const run = async (mode: 'cancel' | 'retry') => {
    setBusy(true)
    setError('')
    try {
      if (mode === 'cancel') await cancelTask(taskId)
      else await retryTask(taskId)
      onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="retry-controls">
      <button className="btn-secondary" onClick={() => run('cancel')} disabled={!canCancel || busy}>取消</button>
      <button className="btn-primary" onClick={() => run('retry')} disabled={!canRetry || busy}>重试</button>
      {error && <span>{error}</span>}
    </div>
  )
}
