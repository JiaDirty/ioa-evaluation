import { useState, useEffect } from 'react'
import { Card } from '../components/Card'
import { ProgressBar } from '../components/ProgressBar'
import { runExperiment } from '../api/client'
import { useWebSocket } from '../hooks/useWebSocket'
import type { JudgeStatus, WSResultMessage } from '../types'

const CATEGORIES = [
  { id: 'trust_authorization', label: 'C1: 信任与授权失灵' },
  { id: 'protocol_interop', label: 'C2: 协议互操作失配' },
  { id: 'interconnection', label: 'C3: 互联扩散与可推断性' },
  { id: 'public_knowledge', label: 'C4: 公共知识失真' },
  { id: 'power_imbalance', label: 'C5: 生态权力失衡' },
  { id: 'human_agency', label: 'C6: 人机能动性侵蚀' },
]

const TOPOLOGIES = ['full_mesh', 'star', 'chain']

const STATUS_LABELS: Record<JudgeStatus, string> = {
  NOT_TRIGGERED: '未触发',
  ATTEMPTED_BLOCKED: '已尝试并阻断',
  PARTIAL_SUCCESS: '部分成功',
  SUCCESS: '成功',
  SUCCESS_WITH_IMPACT: '成功且有后果',
  INDETERMINATE: '证据不足',
}

function formatStatus(status?: string): string {
  if (!status) return '等待 Judge'
  return STATUS_LABELS[status as JudgeStatus] || status
}

function statusClass(status?: string): string {
  if (status === 'SUCCESS' || status === 'SUCCESS_WITH_IMPACT') return 'danger'
  if (status === 'PARTIAL_SUCCESS') return 'warning'
  if (status === 'ATTEMPTED_BLOCKED') return 'success'
  if (status === 'NOT_TRIGGERED') return 'neutral'
  return 'muted'
}

export function ExperimentControl() {
  const [mode, setMode] = useState<'all' | 'category' | 'single'>('all')
  const [category, setCategory] = useState('trust_authorization')
  const [testId, setTestId] = useState('ioa_trust_authorization_001')
  const [topology, setTopology] = useState('full_mesh')
  const [executionMode, setExecutionMode] = useState<'offline_deterministic' | 'agentic_live'>('agentic_live')
  const [running, setRunning] = useState(false)
  const [expId, setExpId] = useState<string | null>(null)

  const { messages } = useWebSocket(expId)

  const progress = messages.find(m => m.type === 'progress')
  const results = messages.filter((m): m is WSResultMessage => m.type === 'result')
  const isComplete = messages.some(m => m.type === 'complete')

  useEffect(() => {
    if (isComplete && running) {
      setRunning(false)
    }
  }, [isComplete, running])

  const handleRun = async () => {
    setRunning(true)
    try {
      const res = await runExperiment({
        mode,
        category: mode === 'category' ? category : undefined,
        test_id: mode === 'single' ? testId : undefined,
        topology,
        execution_mode: executionMode,
      })
      setExpId(res.experiment_id)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Unknown error'
      alert(`启动失败: ${msg}`)
      setRunning(false)
    }
  }

  return (
    <div className="experiment-layout">
      <Card title="实验配置">
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>运行模式</div>
          <div className="pill-group">
            {(['all', 'category', 'single'] as const).map(m => (
              <span key={m} className={`pill ${mode === m ? 'active' : ''}`} onClick={() => setMode(m)}>
                {m === 'all' ? '全部 Seed' : m === 'category' ? '按类别' : '单个 Seed'}
              </span>
            ))}
          </div>
        </div>

        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>执行链路</div>
          <div className="pill-group">
            <span
              className={`pill ${executionMode === 'offline_deterministic' ? 'active' : ''}`}
              onClick={() => setExecutionMode('offline_deterministic')}
            >
              离线框架检查
            </span>
            <span
              className={`pill ${executionMode === 'agentic_live' ? 'active' : ''}`}
              onClick={() => setExecutionMode('agentic_live')}
            >
              真实模型测评
            </span>
          </div>
        </div>

        {mode === 'category' && (
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>测试类别</div>
            <select
              value={category}
              onChange={e => setCategory(e.target.value)}
              style={{ width: '100%', padding: 8, borderRadius: 6, border: '1px solid var(--border)' }}
            >
              {CATEGORIES.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
            </select>
          </div>
        )}

        {mode === 'single' && (
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>Seed ID / Attack Type / 文件名</div>
            <input
              value={testId}
              onChange={e => setTestId(e.target.value)}
              style={{ width: '100%', padding: 8, borderRadius: 6, border: '1px solid var(--border)' }}
            />
          </div>
        )}

        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>拓扑模式</div>
          <div className="pill-group">
            {TOPOLOGIES.map(t => (
              <span key={t} className={`pill ${topology === t ? 'active' : ''}`} onClick={() => setTopology(t)}>
                {t === 'full_mesh' ? '全连接' : t === 'star' ? '星型' : '链式'}
              </span>
            ))}
          </div>
        </div>

        <button className="btn-primary" onClick={handleRun} disabled={running} style={{ width: '100%' }}>
          {running ? '运行中...' : '运行 Judge 实验'}
        </button>
      </Card>

      <div>
        <Card title="运行进度" style={{ marginBottom: 16 }}>
          <ProgressBar current={progress?.current || 0} total={progress?.total || 0} />
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 8 }}>
            {progress ? `当前: ${progress.test_id}` : '等待启动...'}
          </div>
        </Card>

        {results.length > 0 && (
          <Card title="Judge 结果流" style={{ marginBottom: 16 }}>
            <div className="judge-result-stream">
              {results.map((r, i) => (
                <div key={i} className="judge-result-row">
                  <code>{r.test_id}</code>
                  <span>{r.attack_type || '-'}</span>
                  <span className={`judge-badge judge-badge-${statusClass(r.judge_status)}`}>
                    {formatStatus(r.judge_status)}
                  </span>
                  <span>{r.maximum_stage || '-'}</span>
                  <span>{r.evidence_ids?.length || 0} evidence</span>
                </div>
              ))}
            </div>
          </Card>
        )}

        {isComplete && (
          <Card>
            <div style={{ textAlign: 'center', color: 'var(--color-green)', fontWeight: 600 }}>
              实验完成。请在测试仪表盘查看 Judge 状态、最大阶段和证据时间线。
            </div>
          </Card>
        )}
      </div>
    </div>
  )
}
