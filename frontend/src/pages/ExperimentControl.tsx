import { useState, useEffect } from 'react'
import { Card } from '../components/Card'
import { ProgressBar } from '../components/ProgressBar'
import { runExperiment } from '../api/client'
import { useWebSocket } from '../hooks/useWebSocket'
import type { WSResultMessage } from '../types'

const CATEGORIES = [
  { id: 'trust_authorization', label: 'C1: 信任与授权失灵' },
  { id: 'protocol_interop', label: 'C2: 协议互操作失配' },
  { id: 'interconnection', label: 'C3: 互联扩散与可推断性' },
  { id: 'public_knowledge', label: 'C4: 公共知识失真' },
  { id: 'power_imbalance', label: 'C5: 生态权力失衡' },
  { id: 'human_agency', label: 'C6: 人机能动性侵蚀' },
]

const TOPOLOGIES = ['full_mesh', 'star', 'chain']

export function ExperimentControl() {
  const [mode, setMode] = useState<'all' | 'category' | 'single'>('all')
  const [category, setCategory] = useState('trust_authorization')
  const [topology, setTopology] = useState('full_mesh')
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
        topology,
      })
      setExpId(res.experiment_id)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Unknown error'
      alert(`启动失败: ${msg}`)
      setRunning(false)
    }
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: 16 }}>
      <Card title="实验配置">
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>运行模式</div>
          <div className="pill-group">
            {(['all', 'category', 'single'] as const).map(m => (
              <span key={m} className={`pill ${mode === m ? 'active' : ''}`} onClick={() => setMode(m)}>
                {m === 'all' ? '全部测试' : m === 'category' ? '按类别' : '单个测试'}
              </span>
            ))}
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
          {running ? '⏳ 运行中...' : '▶ 运行实验'}
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
          <Card title="测试结果" style={{ marginBottom: 16 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {results.map((r, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
                  <span className={r.passed ? 'log-pass' : 'log-fail'}>
                    {r.passed ? '✓' : '✗'}
                  </span>
                  <code style={{ fontSize: 12 }}>{r.test_id}</code>
                  <span className={`badge badge-${r.risk_level.toLowerCase()}`}>{r.risk_level}</span>
                </div>
              ))}
            </div>
          </Card>
        )}

        {isComplete && (
          <Card>
            <div style={{ textAlign: 'center', color: 'var(--color-green)', fontWeight: 600 }}>
              ✅ 实验完成！请查看仪表盘和反馈循环页面。
            </div>
          </Card>
        )}
      </div>
    </div>
  )
}
