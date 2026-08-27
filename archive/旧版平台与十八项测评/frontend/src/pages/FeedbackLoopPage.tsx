import { Card } from '../components/Card'
import { Spinner } from '../components/Spinner'
import { ErrorBanner } from '../components/ErrorBanner'
import { useApi } from '../hooks/useApi'
import { getFeedbackSummary, getFeedbackActions } from '../api/client'

const RISK_EMOJI: Record<string, string> = {
  HIGH: '🔴',
  MEDIUM: '🟡',
  LOW: '🟢',
}

export function FeedbackLoopPage() {
  const { data: summary, loading: summaryLoading, error: summaryError, reload: reloadSummary } = useApi(
    (signal) => getFeedbackSummary(signal),
    []
  )
  const { data: actions, loading: actionsLoading, error: actionsError, reload: reloadActions } = useApi(
    (signal) => getFeedbackActions(signal),
    []
  )

  if (summaryLoading || actionsLoading) return <Spinner />
  if (summaryError) return <ErrorBanner message={`加载反馈摘要失败: ${summaryError}`} onRetry={reloadSummary} />
  if (actionsError) return <ErrorBanner message={`加载反馈动作失败: ${actionsError}`} onRetry={reloadActions} />
  if (!summary || !summary.total_tests) return <div className="empty-state">暂无数据，请先运行实验</div>

  const dimensions = Object.entries(summary.dimensions || {})

  return (
    <div>
      <div className="grid-4" style={{ marginBottom: 20 }}>
        <Card>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--color-blue)' }}>{summary.total_tests}</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>总测试</div>
          </div>
        </Card>
        <Card>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--color-green)' }}>{summary.total_passed}</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>通过</div>
          </div>
        </Card>
        <Card>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--color-red)' }}>{summary.total_failed}</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>失败</div>
          </div>
        </Card>
        <Card>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--color-red)' }}>{summary.critical_actions}</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>关键反馈</div>
          </div>
        </Card>
      </div>

      <div className="grid-2" style={{ marginBottom: 20 }}>
        <Card title="风险维度报告">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {dimensions.map(([key, dim]) => (
              <div key={key} style={{
                display: 'flex', alignItems: 'center', gap: 8, padding: 10,
                background: dim.risk_level === 'HIGH' ? 'var(--color-red-light)' : dim.risk_level === 'MEDIUM' ? '#fff8f0' : 'var(--color-green-light)',
                borderRadius: 6,
                border: `1px solid ${dim.risk_level === 'HIGH' ? 'var(--color-red)' : dim.risk_level === 'MEDIUM' ? 'var(--color-yellow)' : 'var(--color-green)'}`,
              }}>
                <span style={{ fontSize: 16 }}>{RISK_EMOJI[dim.risk_level] || '⚪'}</span>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 600, fontSize: 13 }}>{dim.name}</div>
                  <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{dim.risk_level} — {dim.pass_rate} 通过</div>
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card title={`反馈动作 (${(actions || []).length} 项)`}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {(actions || []).slice(0, 10).map(action => (
              <div key={action.action_id} className={`feedback-card feedback-${action.priority}`}>
                <div style={{
                  fontWeight: 600, fontSize: 12,
                  color: action.priority === 'critical' ? 'var(--color-red)' : action.priority === 'high' ? 'var(--color-yellow)' : 'var(--text-secondary)',
                }}>
                  {action.priority.toUpperCase()}
                </div>
                <div style={{ fontSize: 12, marginTop: 4 }}>{action.description}</div>
              </div>
            ))}
            {(!actions || actions.length === 0) && <div style={{ color: 'var(--text-secondary)', fontSize: 13 }}>暂无反馈动作</div>}
          </div>
        </Card>
      </div>

      <Card title="修复建议">
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          {dimensions.flatMap(([key, dim]) =>
            dim.recommendations.map((rec, i) => (
              <div key={`${key}-${i}`} style={{ padding: 10, background: 'var(--color-blue-light)', borderRadius: 6, border: '1px solid #d0e3f7' }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-blue)' }}>{dim.name}</div>
                <div style={{ fontSize: 12, marginTop: 4 }}>{rec}</div>
              </div>
            ))
          )}
        </div>
      </Card>
    </div>
  )
}
